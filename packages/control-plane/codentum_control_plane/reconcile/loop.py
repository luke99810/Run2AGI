"""ReconcileLoop —— 控制平面的调和循环。

对标 K8s controller manager。对每个非终态的 WorkPacket，
检查当前状态 → 判断下一步 → 推进状态 → 落盘。

★ 幂等：同一状态跑 N 次 tick 产生同样的结果（副作用只发生一次）。
★ 确定性：零 LLM，零随机，所有判定由代码做出。
★ 不 import codentum_harness：WorkerRuntime 通过构造函数注入。
★ 崩溃恢复：load_state() 从 .codentum/ 目录重建全部内存状态。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from codentum_contracts.interfaces import (
    AbortReason,
    BudgetGrantRuntime,
    MountSpec,
    SpawnRequest,
    WorkerHandle,
    WorkerOutcome,
    WorkerRuntime,
)
from codentum_contracts.state import (
    DependencyGraph,
    ModelRouting,
    EvidenceRef,
    OwnershipGraph,
    PacketId,
    PacketState,
    WorkPacket,
    dump_state,
)
from codentum_control_plane.locks import LockTable
from codentum_control_plane.gates import GateRunner, register_builtin_gates
from codentum_control_plane.budget import BudgetTracker
from codentum_control_plane.guardian import Guardian
from codentum_control_plane.state_machine import TransitionTable

from .actions import PacketTransition, ReconcileContext, TickReport


TERMINAL_STATES: frozenset[PacketState] = frozenset({"accepted", "abandoned"})
"""终态包不再被 reconcile 处理。"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ReconcileLoop:
    """调和循环 —— 控制平面的心脏。

    用法：
        loop = ReconcileLoop(state_dir="path/to/.codentum")
        loop.load_state()
        report = loop.tick()          # 推进一轮
        loop.save_state()             # 落盘
    """

    state_dir: str
    "包含 graph.json 和 packets/ 的目录（通常是 .codentum/）。"

    worker_runtime: WorkerRuntime | None = None
    "★ 可选 —— 没有 Worker 时仍可推进依赖、锁等纯确定性状态。"

    transition_table: TransitionTable | None = None
    "★ 若为 None，使用不含角色转换的空表（仅允许 reconcile 内部转换）。"

    gate_runner: GateRunner | None = None
    "★ 门禁执行器。若为 None，review→accepted 仅检查证据是否存在。"

    budget_tracker: BudgetTracker | None = None
    "★ 全局预算追踪器。若为 None，不记录支出也不做全局预算检查。"

    guardian: Guardian | None = None
    "★ 确定性拦截器。若为 None，不执行运行时不变量检查。"

    # ── 内部状态（由 load_state() 填充）─────────────────────

    _packets: dict[PacketId, WorkPacket] = field(default_factory=dict, init=False)
    _dep_graph: DependencyGraph | None = field(default_factory=lambda: None, init=False)
    _lock_table: LockTable = field(default_factory=LockTable, init=False)
    _active_workers: dict[PacketId, WorkerHandle] = field(default_factory=dict, init=False)
    _tick_count: int = field(default=0, init=False)
    _dirty: bool = field(default=False, init=False)

    _loop: object = field(default=None, init=False, repr=False)
    """Persistent event loop shared between spawn() and settle() calls.
    
    Must be a single long-lived loop (not asyncio.run() which creates/destroys
    per call) because B's LocalWorkerRuntime spawns background tasks during
    spawn() that must survive until settle().
    """

    # ════════════════════════════════════════════════════════════
    #  状态加载 / 持久化
    # ════════════════════════════════════════════════════════════

    def load_state(self, *, state_dir: str | None = None) -> None:
        """从 .codentum/ 目录加载全部状态。

        ★ 崩溃恢复的唯一入口。只要 .codentum/ 里的文件没坏，
          重启后调用此方法即可恢复。
        """
        if state_dir is not None:
            self.state_dir = state_dir

        root = Path(self.state_dir)

        # 1. 加载 graph.json → 依赖图 + 所有权图
        graph_path = root / "graph.json"
        if graph_path.exists():
            graph_raw = json.loads(graph_path.read_text("utf-8"))
            dep_raw = graph_raw.get("dependency", {})
            self._dep_graph = DependencyGraph(
                nodes=tuple(dep_raw.get("nodes", ())),
                edges=tuple(
                    {"from": e["from"], "to": e["to"]}
                    for e in dep_raw.get("edges", ())
                ),
            )
            ownership = OwnershipGraph(
                locks=tuple(
                    {
                        "pathPrefix": lk["pathPrefix"],
                        "heldBy": lk["heldBy"],
                        "acquiredAt": lk["acquiredAt"],
                    }
                    for lk in graph_raw.get("ownership", {}).get("locks", ())
                ),
                version=graph_raw.get("ownership", {}).get("version", 0),
            )
            self._lock_table = LockTable.from_ownership(ownership)
        else:
            self._dep_graph = DependencyGraph(nodes=(), edges=())
            self._lock_table = LockTable()

        # 2. 加载所有 packet 文件
        packets_dir = root / "packets"
        self._packets.clear()
        if packets_dir.exists():
            for pf in sorted(packets_dir.glob("*.json")):
                raw = json.loads(pf.read_text("utf-8"))
                packet = WorkPacket(
                    id=raw["id"],
                    kind=raw["kind"],
                    state=raw["state"],
                    role=raw["role"],
                    ownsPaths=tuple(raw.get("ownsPaths", ())),
                    readsPaths=tuple(raw.get("readsPaths", ())),
                    deps=tuple(raw.get("deps", ())),
                    acceptance={
                        "kind": raw["acceptance"]["kind"],
                        "predicate": raw["acceptance"]["predicate"],
                        "threshold": raw["acceptance"].get("threshold"),
                        "authoredBy": raw["acceptance"]["authoredBy"],
                    },
                    budget={
                        "currency": raw["budget"]["currency"],
                        "limitCny": raw["budget"]["limitCny"],
                        "spentCny": raw["budget"]["spentCny"],
                        "degradationChain": tuple(raw["budget"].get("degradationChain", ())),
                    },
                    routing=(
                        {
                            "model": raw["routing"]["model"],
                            "effort": raw["routing"]["effort"],
                            "batch": raw["routing"].get("batch"),
                        }
                        if raw.get("routing")
                        else None
                    ),
                    attempts=raw.get("attempts", 0),
                    evidence=tuple(raw.get("evidence", ())),
                    provenance={
                        "createdBy": raw["provenance"]["createdBy"],
                        "createdAt": raw["provenance"]["createdAt"],
                        "parent": raw["provenance"].get("parent"),
                    },
                )
                self._packets[packet.id] = packet

        self._active_workers.clear()
        self._tick_count = 0
        self._dirty = False

    def save_state(self) -> None:
        """将当前内存状态写回 .codentum/ 文件。

        ★ 仅当 _dirty=True 时才实际写入。
        ★ 先写 packets 再写 graph —— graph 是依赖图和锁表的汇总。
        """
        if not self._dirty:
            return

        root = Path(self.state_dir)
        packets_dir = root / "packets"
        packets_dir.mkdir(parents=True, exist_ok=True)

        # 写每个 packet 文件
        for pid, packet in self._packets.items():
            pf = packets_dir / f"{pid}.json"
            pf.write_text(
                json.dumps(dump_state(packet), indent=2, ensure_ascii=False) + "\n",
                "utf-8",
            )

        # 写 graph.json（依赖图 + 所有权图）
        ownership = self._lock_table.to_ownership()
        graph_raw: dict[str, Any] = {
            "schemaVersion": 1,
            "dependency": {
                "nodes": list(self._dep_graph.nodes) if self._dep_graph else [],
                "edges": [
                    {"from": e.from_, "to": e.to}
                    for e in (self._dep_graph.edges if self._dep_graph else ())
                ],
            },
            "ownership": {
                "locks": [
                    {
                        "pathPrefix": lk.pathPrefix,
                        "heldBy": lk.heldBy,
                        "acquiredAt": lk.acquiredAt,
                    }
                    for lk in ownership.locks
                ],
                "version": ownership.version,
            },
        }
        (root / "graph.json").write_text(
            json.dumps(graph_raw, indent=2, ensure_ascii=False) + "\n",
            "utf-8",
        )

        self._dirty = False

    # ════════════════════════════════════════════════════════════
    #  调和主循环
    # ════════════════════════════════════════════════════════════

    def tick(self) -> TickReport:
        """执行一轮调和。

        遍历所有非终态 packet，每条最多推进一次。
        返回本轮实际发生的状态变更。
        """
        self._tick_count += 1
        transitions: list[PacketTransition] = []
        errors: list[str] = []

        # 构建依赖状态缓存（一次遍历，后续所有 packet 复用）
        dep_states = {pid: p.state for pid, p in self._packets.items()}
        ctx = ReconcileContext(dep_states=dep_states)

        for pid in sorted(self._packets):
            packet = self._packets[pid]
            if packet.state in TERMINAL_STATES:
                continue

            try:
                t = self._reconcile_one(packet, ctx)
                if t is not None:
                    transitions.append(t)
                    self._dirty = True
            except Exception as exc:
                errors.append(f"{pid} ({packet.state}): {exc}")

        # 收集 guardian 拦截记录
        for ic in (self.guardian.drain_interceptions() if self.guardian else ()):
            errors.append(f"[{ic.code}] {ic.packet_id}: {ic.detail}")

        return TickReport(transitions=tuple(transitions), errors=tuple(errors))

    def run_until_stable(self, *, max_ticks: int = 20) -> TickReport:
        """反复 tick 直到没有更多状态可推进，或达到最大轮数。

        ★ 用于测试：load_state → run_until_stable → 断言终态。
        """
        all_transitions: list[PacketTransition] = []
        all_errors: list[str] = []

        for _ in range(max_ticks):
            report = self.tick()
            if not report.transitions and not report.errors:
                break
            all_transitions.extend(report.transitions)
            all_errors.extend(report.errors)

        return TickReport(transitions=tuple(all_transitions), errors=tuple(all_errors))

    # ════════════════════════════════════════════════════════════
    #  单 packet 调和
    # ════════════════════════════════════════════════════════════

    def _reconcile_one(
        self, packet: WorkPacket, ctx: ReconcileContext
    ) -> PacketTransition | None:
        """对单个 packet 尝试推进状态。返回实际发生的转换或 None。"""

        state = packet.state

        if state == "pending":
            return self._try_pending_to_ready(packet, ctx)
        elif state == "ready":
            return self._try_ready_to_running(packet)
        elif state == "running":
            return self._try_running_to_review(packet)
        elif state == "blocked":
            return self._try_blocked_to_ready(packet, ctx)
        elif state == "review":
            return self._try_review_to_accepted(packet)

        return None

    # ── pending → ready ──────────────────────────────────────

    def _try_pending_to_ready(
        self, packet: WorkPacket, ctx: ReconcileContext
    ) -> PacketTransition | None:
        """pending → ready：所有依赖都已 accepted。"""
        unsatisfied: list[PacketId] = []
        for dep_id in packet.deps:
            dep_state = ctx.dep_states.get(dep_id)
            if dep_state != "accepted":
                unsatisfied.append(dep_id)
                # 如果依赖不在已知 packet 中，也视为未满足（外部依赖需人工确认）
                if dep_id not in self._packets:
                    unsatisfied.append(dep_id)

        if unsatisfied:
            return None  # 静默跳过 ——「还在等」不是错误

        return self._apply_transition(
            packet,
            target="ready",
            detail=f"所有 {len(packet.deps)} 个依赖均已 accepted",
            evidence_refs=(),
        )

    # ── ready → running ──────────────────────────────────────

    def _try_ready_to_running(self, packet: WorkPacket) -> PacketTransition | None:
        """ready → running：获取路径锁，生成 worker 实例。

        两条路：
        (a) 无 WorkerRuntime → 仅获取锁，不实际派发。
            状态仍推到 running，因为没有锁就不能放别的 ready。
        (b) 有 WorkerRuntime → 获取锁 + spawn worker。
        """
        if not packet.ownsPaths:
            return self._apply_transition(
                packet, target="blocked",
                detail="ready 但 ownsPaths 为空，无写权限无法执行",
                evidence_refs=(),
            )

        # 获取路径锁
        now = _now_iso()
        # 所有权图版本使用锁表当前版本（乐观锁基础）
        result = self._lock_table.acquire(
            packet.id,
            packet.ownsPaths,
            at=now,
            expected_version=self._lock_table.version,
        )

        if not result.ok:
            # 锁冲突 —— 下轮再试（或者转为 blocked）
            return None

        # 生成系统证据
        ev = f"sys:lock:{packet.id}:{self._tick_count}"
        evidence = (EvidenceRef(ev),)

        # Guardian 拦截：I5 尝试次数检查
        if self.guardian is not None:
            ok, reason = self.guardian.check_attempts(packet)
            if not ok:
                return self._apply_transition(
                    packet, target="abandoned",
                    detail=reason,
                    evidence_refs=(),
                )

        # 全局预算检查：是否有足够余额
        if self.budget_tracker is not None:
            if not self.budget_tracker.can_afford(packet.budget.limitCny):
                # 预算不足 → blocked
                return self._apply_transition(
                    packet, target="blocked",
                    detail=(
                        f"全局预算不足：剩余 ${self.budget_tracker.remaining:.2f}，"
                        f"需要 ${packet.budget.limitCny:.2f}"
                    ),
                    evidence_refs=(),
                )

        # 如果有 WorkerRuntime，尝试 spawn
        if self.worker_runtime is not None:
            # ★ 确保持久 event loop 已创建
            # 不能用 asyncio.run() 因为 B 的 LocalWorkerRuntime 在 spawn() 中创建
            # 后台 task，必须用同一个 loop 在 settle() 中等待
            if self._loop is None:
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
            try:
                # 异步 spawn —— reconcile 是同步循环，
                # 这里使用同步适配（由 WorkerRuntime 实现者保证 spawn 立即返回句柄）
                # 注意：settle() 在 _try_running_to_review 中异步等待。
                handle = self._loop.run_until_complete(
                    self.worker_runtime.spawn(
                        self._build_spawn_request(packet)
                    )
                )
                self._active_workers[packet.id] = handle
            except Exception as exc:
                # spawn 失败 —— 释放锁，回退
                self._lock_table.release(packet.id)
                return None

        return self._apply_transition(
            packet,
            target="running",
            detail=f"锁已获取：{', '.join(packet.ownsPaths[:3])}"
            + (f"（共 {len(packet.ownsPaths)} 条）" if len(packet.ownsPaths) > 3 else ""),
            evidence_refs=evidence,
        )

    # ── running → review / blocked ───────────────────────────

    def _try_running_to_review(self, packet: WorkPacket) -> PacketTransition | None:
        """检查 running packet 的 worker 是否已经结束。

        没有 WorkerRuntime 时 → 跳过（无法判定是否完成，留给外部驱动）。
        """
        if self.worker_runtime is None:
            return None

        handle = self._active_workers.get(packet.id)
        if handle is None:
            # worker 句柄丢失（崩溃恢复后）→ 尝试重新接管
            return None

        try:
            outcome = self._loop.run_until_complete(self.worker_runtime.settle(handle))
        except Exception:
            # Worker 还在跑或出错了 —— 下轮再看
            return None

        # 释放锁（无论成败）
        self._lock_table.release(packet.id)
        self._active_workers.pop(packet.id, None)

        if hasattr(outcome, "status") and outcome.status == "completed":
            # 记录支出到全局预算
            if self.budget_tracker is not None and hasattr(outcome, "spent_cny"):
                spent = getattr(outcome, "spent_cny", 0.0)
                if spent > 0:
                    self.budget_tracker.spend(
                        spent,
                        role=packet.role,
                        model=packet.routing.model if packet.routing else "",
                    )

            return self._apply_transition(
                packet,
                target="review",
                detail=f"Worker 完成，spent=${outcome.spent_cny:.4f}",
                evidence_refs=tuple(outcome.evidence) if outcome.evidence else (),
                extra_updates={"attempts": packet.attempts + 1},
            )
        elif hasattr(outcome, "status") and outcome.status == "failed":
            # 失败也记录支出（已经花了钱）
            if self.budget_tracker is not None and hasattr(outcome, "spent_cny"):
                spent = getattr(outcome, "spent_cny", 0.0)
                if spent > 0:
                    self.budget_tracker.spend(
                        spent,
                        role=packet.role,
                        model=packet.routing.model if packet.routing else "",
                    )

            return self._apply_transition(
                packet,
                target="review",
                detail=f"Worker 失败 ({outcome.reason_code})，进入评审",
                evidence_refs=tuple(outcome.evidence) if hasattr(outcome, 'evidence') else (),
                extra_updates={"attempts": packet.attempts + 1},
            )
        else:
            # aborted
            return self._apply_transition(
                packet, target="blocked",
                detail=f"Worker 被中止: {outcome.reason}",
                evidence_refs=(),
            )

    # ── blocked → ready ──────────────────────────────────────

    def _try_blocked_to_ready(
        self, packet: WorkPacket, ctx: ReconcileContext
    ) -> PacketTransition | None:
        """blocked → ready：阻塞条件是否已解除。

        当前最简单的判据：依赖是否已满足 + 是否有可用的写路径。
        更复杂的阻塞类型（如「等待人工审批」）留到 P1。
        """
        # 如果阻塞原因是依赖未满足，现在再检查一次
        unsatisfied = [
            d for d in packet.deps
            if ctx.dep_states.get(d) != "accepted"
        ]
        if unsatisfied:
            return None  # 还在等

        return self._apply_transition(
            packet, target="ready",
            detail="阻塞条件已解除，可重新调度",
            evidence_refs=(),
        )

    # ── review → accepted / rejected ─────────────────────────

    def _try_review_to_accepted(self, packet: WorkPacket) -> PacketTransition | None:
        """review → accepted：运行门禁判定。

        优先使用 gate_runner（若已配置），否则退回到简单证据检查。
        """
        if self.gate_runner is not None:
            # 用 gate_runner 跑门禁
            gate_id = "review"
            if self.transition_table is not None:
                # 查 TransitionTable 看需要哪个门禁
                try:
                    verdict = self.transition_table.check(
                        role=packet.role,
                        current="review",
                        target="accepted",
                        evidence=packet.evidence,
                    )
                    if not verdict.allowed:
                        return None
                    if verdict.requires_gate:
                        gate_id = verdict.requires_gate
                except Exception:
                    pass  # TransitionTable 不可用时退回到默认门禁

            gate_result = self.gate_runner.check(gate_id, packet)
            if not gate_result.passed:
                return None

            return self._apply_transition(
                packet,
                target="accepted",
                detail=f"门禁 {gate_id!r} 通过：{gate_result.detail}",
                evidence_refs=gate_result.evidence_refs,
            )

        # 退回到简单证据检查
        if not packet.evidence:
            return None

        return self._apply_transition(
            packet,
            target="accepted",
            detail=f"验收门禁通过（{len(packet.evidence)} 条证据）",
            evidence_refs=packet.evidence,
        )

    # ════════════════════════════════════════════════════════════
    #  内部辅助
    # ════════════════════════════════════════════════════════════

    def _apply_transition(
        self,
        packet: WorkPacket,
        *,
        target: PacketState,
        detail: str,
        evidence_refs: Sequence[EvidenceRef],
        extra_updates: dict[str, object] | None = None,
    ) -> PacketTransition:
        """原子地更新 packet 状态并返回转换记录。

        ★ 这就是 reconcile 唯一修改状态的地方。
        """
        old_state = packet.state

        updates: dict[str, object] = {"state": target}
        if evidence_refs:
            updates["evidence"] = tuple(evidence_refs)
        if extra_updates:
            updates.update(extra_updates)

        new_packet = packet.model_copy(update=updates)
        self._packets[packet.id] = new_packet

        return PacketTransition(
            packet_id=packet.id,
            from_state=old_state,
            to_state=target,
            detail=detail,
        )

    def _build_spawn_request(self, packet: WorkPacket) -> SpawnRequest:
        """从 WorkPacket 构造 SpawnRequest。

        ★ 控制平面不接触 RoleSpec —— tools 留空，
          WorkerRuntime 实现负责从 RoleSpec 派生工具面。
        """
        mounts: list[MountSpec] = []

        # 写权限路径
        for p in packet.ownsPaths:
            mounts.append(MountSpec(host_path=p, mount_path=p, mode="rw"))

        # 只读挂载路径
        for p in packet.readsPaths:
            mounts.append(MountSpec(host_path=p, mount_path=p, mode="ro"))

        return SpawnRequest(
            packet_id=packet.id,
            role=packet.role,
            mounts=tuple(mounts),
            tools=(),  # ★ WorkerRuntime 负责填
            routing=packet.routing if packet.routing else ModelRouting(model="default", effort="medium"),
            budget=BudgetGrantRuntime(
                limit_cny=packet.budget.limitCny,
                degradation_chain=packet.budget.degradationChain,
            ),
            workspace=str(
                Path(self.state_dir).parent.parent / "codentum-workers" / packet.id / f"attempt-{packet.attempts + 1}"
            ),
            attempt=packet.attempts + 1,
        )

    # ════════════════════════════════════════════════════════════
    #  查询（给测试和桌面端用）
    # ════════════════════════════════════════════════════════════

    @property
    def packets(self) -> Mapping[PacketId, WorkPacket]:
        return self._packets

    @property
    def packet_count(self) -> int:
        return len(self._packets)

    def packet(self, pid: PacketId) -> WorkPacket:
        return self._packets[pid]
