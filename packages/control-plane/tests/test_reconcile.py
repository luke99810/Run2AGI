"""reconcile loop 测试

覆盖：
  · 从 golden-state fixture 加载并推进到终态
  · pending → ready（依赖满足）
  · ready → running（锁获取）
  · running → review（worker 完成）
  · review → accepted（门禁通过）
  · blocked → ready（阻塞解除）
  · 幂等性（同一状态多次 tick 无副作用）
  · 无 WorkerRuntime 时仍可推进纯确定性状态
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
from collections.abc import Iterator
from typing import Any
from pathlib import Path

import pytest

from codentum_contracts.state import (
    Acceptance,
    BudgetGrant,
    EvidenceRef,
    PacketId,
    Provenance,
    WorkPacket,
    dump_state,
)
from collections.abc import AsyncIterator

from codentum_contracts.interfaces import (
    AbortReason,
    SpawnRequest,
    WorkerAborted,
    CheckpointRef,
    WorkerCompleted,
    WorkerEvent,
    WorkerFailed,
    WorkerHandle,
    WorkerOutcome,
    WorkerRuntime,
    FailureCode,
)
from codentum_control_plane.locks import LockTable
from codentum_control_plane.state_machine import TransitionTable
from codentum_control_plane.reconcile import ReconcileLoop, PacketTransition, TickReport
from codentum_control_plane.gates import GateRunner


# ════════════════════════════════════════════════════════════════
#  Fixtures
# ════════════════════════════════════════════════════════════════

FIXTURES_ROOT = Path(__file__).resolve().parents[3] / "fixtures" / "golden-state"


def _copy_fixture(name: str, dst: Path) -> None:
    """把某个 golden-state 快照复制到临时目录。"""
    src = FIXTURES_ROOT / name / ".codentum"
    if not src.exists():
        pytest.skip(f"fixture {name} 不存在: {src}")
    # 只复制 packets 和 graph.json
    for item in src.iterdir():
        if item.is_dir():
            shutil.copytree(item, dst / item.name)
        else:
            shutil.copy2(item, dst / item.name)


@pytest.fixture
def tmp_state_dir() -> Iterator[Path]:
    """创建临时的 .codentum/ 目录。"""
    with tempfile.TemporaryDirectory() as td:
        state_dir = Path(td) / ".codentum"
        state_dir.mkdir()
        yield state_dir


@pytest.fixture
def empty_loop(tmp_state_dir: Path) -> ReconcileLoop:
    """从空状态启动的 reconcile loop。"""
    loop = ReconcileLoop(state_dir=str(tmp_state_dir))
    loop._dep_graph = None
    return loop


@pytest.fixture
def mid_flight_loop(tmp_state_dir: Path) -> Iterator[ReconcileLoop]:
    """加载 mid-flight fixture 的 reconcile loop。"""
    _copy_fixture("mid-flight", tmp_state_dir)
    loop = ReconcileLoop(state_dir=str(tmp_state_dir))
    loop.load_state()
    yield loop


@pytest.fixture
def blocked_loop(tmp_state_dir: Path) -> Iterator[ReconcileLoop]:
    """加载 blocked fixture 的 reconcile loop。"""
    _copy_fixture("blocked", tmp_state_dir)
    loop = ReconcileLoop(state_dir=str(tmp_state_dir))
    loop.load_state()
    yield loop


# ════════════════════════════════════════════════════════════════
#  Helper：手动创建一个 WorkPacket 并注册到 loop
# ════════════════════════════════════════════════════════════════

def _make_packet(
    pid: str,
    state: str = "pending",
    deps: tuple[str, ...] = (),
    owns: tuple[str, ...] = ("src/test/",),
    role: str = "coder",
    kind: str = "impl",
) -> WorkPacket:
    return WorkPacket(
        id=PacketId(pid),
        kind=kind,  # type: ignore[arg-type]
        state=state,  # type: ignore[arg-type]
        role=role,  # type: ignore[arg-type]
        ownsPaths=owns,
        readsPaths=("tests/",),
        deps=tuple(PacketId(d) for d in deps),
        acceptance=Acceptance(
            kind= "test",
            predicate= "pytest",
            authoredBy= "qa",
        ),
        budget=BudgetGrant(
            currency= "CNY",
            limitCny= 5.0,
            spentCny= 0.0,
            degradationChain= ("drop_semantic",),
        ),
        attempts=0,
        evidence=(),
        provenance=Provenance(
            createdBy= "planner",
            createdAt= "2026-08-05T00:00:00Z",
        ),
    )


def _inject(loop: ReconcileLoop, packet: WorkPacket) -> None:
    """将 packet 注入 reconcile loop 并写回 state_dir。"""
    loop._packets[packet.id] = packet
    # 写入文件，让 save_state / re-load 能工作
    pf = Path(loop.state_dir) / "packets" / f"{packet.id}.json"
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(
        json.dumps(dump_state(packet), indent=2, ensure_ascii=False) + "\n",
        "utf-8",
    )


def _write_scheduling(
    state_dir: Path,
    *,
    running: int = 4,
    review: int = 2,
) -> None:
    (state_dir / "scheduling.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "wipLimits": {
                    "running": running,
                    "review": review,
                },
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


# ════════════════════════════════════════════════════════════════
#  Tests: 状态加载
# ════════════════════════════════════════════════════════════════

class TestLoadState:
    def test_load_mid_flight(self, mid_flight_loop: ReconcileLoop) -> None:
        """加载 mid-flight fixture：应有 5 个 packet，状态各异。"""
        assert mid_flight_loop.packet_count == 5

        states = {p.state for p in mid_flight_loop.packets.values()}
        assert "accepted" in states  # wp-a1c001
        assert "running" in states   # wp-b2d002, wp-c3e003, wp-d4f004
        assert "pending" in states   # wp-e5g005

    def test_load_empty(self, empty_loop: ReconcileLoop) -> None:
        """空状态加载不应崩溃。"""
        assert empty_loop.packet_count == 0
        report = empty_loop.tick()
        assert len(report.transitions) == 0

    def test_load_blocked(self, blocked_loop: ReconcileLoop) -> None:
        """加载 blocked fixture。"""
        assert blocked_loop.packet_count > 0

        states = {p.state for p in blocked_loop.packets.values()}
        assert "accepted" in states
        assert "blocked" in states
        assert "running" in states
        assert "pending" in states


# ════════════════════════════════════════════════════════════════
#  Tests: pending → ready
# ════════════════════════════════════════════════════════════════

class TestPendingToReady:
    def test_no_deps_promotes_immediately(self, empty_loop: ReconcileLoop) -> None:
        """无依赖的 pending packet 第一次 tick 就推进到 ready。"""
        pkt = _make_packet("wp-000001", state="pending")
        _inject(empty_loop, pkt)

        report = empty_loop.tick()
        assert len(report.transitions) == 1
        t = report.transitions[0]
        assert t.packet_id == "wp-000001"
        assert t.from_state == "pending"
        assert t.to_state == "ready"

    def test_dep_not_satisfied_stays_pending(self, empty_loop: ReconcileLoop) -> None:
        """有未满足依赖的 packet 保持 pending。"""
        pkt = _make_packet("wp-000002", state="pending", deps=("wp-9zzzzz",))
        _inject(empty_loop, pkt)

        report = empty_loop.tick()
        assert len(report.transitions) == 0
        assert empty_loop.packet(PacketId("wp-000002")).state == "pending"

    def test_dep_satisfied_promotes(self, empty_loop: ReconcileLoop) -> None:
        """依赖满足后推进到 ready。"""
        dep = _make_packet("wp-dep001", state="accepted", owns=("src/dep/",))
        pkt = _make_packet("wp-main01", state="pending", deps=("wp-dep001",))
        _inject(empty_loop, dep)
        _inject(empty_loop, pkt)

        report = empty_loop.tick()
        assert len(report.transitions) >= 1
        main_transition = [t for t in report.transitions if t.packet_id == "wp-main01"]
        assert len(main_transition) == 1
        assert main_transition[0].to_state == "ready"

    def test_multiple_deps_all_must_be_accepted(self, empty_loop: ReconcileLoop) -> None:
        """多个依赖必须全部 accepted 才推进。"""
        dep1 = _make_packet("wp-d1d1d1", state="accepted", owns=("src/a/",))
        dep2 = _make_packet("wp-d2d2d2", state="accepted", owns=("src/b/",))
        dep3 = _make_packet("wp-d3d3d3", state="running", owns=("src/c/",))  # 还没完
        pkt = _make_packet("wp-main01", state="pending", deps=("wp-d1d1d1", "wp-d2d2d2", "wp-d3d3d3"))
        _inject(empty_loop, dep1)
        _inject(empty_loop, dep2)
        _inject(empty_loop, dep3)
        _inject(empty_loop, pkt)

        report = empty_loop.tick()
        # dep1 → ready, dep2 → ready, dep3 still running, main stays pending
        main = [t for t in report.transitions if t.packet_id == "wp-main01"]
        assert len(main) == 0


# ════════════════════════════════════════════════════════════════
#  Tests: ready → running
# ════════════════════════════════════════════════════════════════

class TestReadyToRunning:
    def test_acquires_lock(self, empty_loop: ReconcileLoop) -> None:
        """ready → running 获取路径锁。"""
        pkt = _make_packet("wp-000003", state="ready", owns=("src/app/",))
        _inject(empty_loop, pkt)

        report = empty_loop.tick()
        assert len(report.transitions) >= 1
        t = [x for x in report.transitions if x.packet_id == "wp-000003"][0]
        assert t.to_state == "running"

        # 锁表里应该有这条锁
        assert empty_loop._lock_table.holder_of("src/app/utils.py") == PacketId("wp-000003")

    def test_lock_conflict_stays_ready(self, empty_loop: ReconcileLoop) -> None:
        """路径被占用时保持 ready。"""
        # 先占住路径
        holder = _make_packet("wp-hold01", state="running", owns=("src/app/",))
        _inject(empty_loop, holder)
        empty_loop._lock_table.acquire(
            PacketId("wp-hold01"), ("src/app/",), at="2026-08-05T00:00:00Z"
        )

        # 竞争同一个路径
        pkt = _make_packet("wp-000004", state="ready", owns=("src/app/",))
        _inject(empty_loop, pkt)

        report = empty_loop.tick()
        transitions_for_pkt = [t for t in report.transitions if t.packet_id == "wp-000004"]
        assert len(transitions_for_pkt) == 0
        assert empty_loop.packet(PacketId("wp-000004")).state == "ready"

    def test_ready_without_owns_paths_blocked(self, empty_loop: ReconcileLoop) -> None:
        """没有写权限路径的 ready packet 转为 blocked。"""
        pkt = _make_packet("wp-000005", state="ready", owns=())
        _inject(empty_loop, pkt)

        report = empty_loop.tick()
        t = [x for x in report.transitions if x.packet_id == "wp-000005"][0]
        assert t.to_state == "blocked"

    def test_running_wip_limit_keeps_excess_ready_in_queue(
        self, empty_loop: ReconcileLoop, tmp_state_dir: Path
    ) -> None:
        """running WIP 满不是 blocked；多出来的 ready 留在队列等下一轮容量。"""
        _write_scheduling(tmp_state_dir, running=1, review=2)
        first = _make_packet("wp-wip001", state="ready", owns=("src/a/",))
        second = _make_packet("wp-wip002", state="ready", owns=("src/b/",))
        _inject(empty_loop, first)
        _inject(empty_loop, second)

        report = empty_loop.tick()

        started = [t.packet_id for t in report.transitions if t.to_state == "running"]
        assert started == ["wp-wip001"]
        assert empty_loop.packet(PacketId("wp-wip001")).state == "running"
        assert empty_loop.packet(PacketId("wp-wip002")).state == "ready"

        projection = json.loads((tmp_state_dir / "scheduling.json").read_text(encoding="utf-8"))
        assert projection["current"]["running"] == 1
        assert projection["current"]["ready"] == 1
        assert projection["capacity"]["running"] == 0

    def test_ready_queue_prefers_critical_path_before_packet_id(
        self, empty_loop: ReconcileLoop, tmp_state_dir: Path
    ) -> None:
        """WIP 只有 1 时，长依赖链的 packet 抢先启动，而不是 id 小者先跑。"""
        _write_scheduling(tmp_state_dir, running=1, review=2)
        short = _make_packet("wp-aaaaaa", state="ready", owns=("src/short/",))
        long = _make_packet("wp-zzzzzz", state="ready", owns=("src/long/",))
        child = _make_packet(
            "wp-child1",
            state="pending",
            deps=("wp-zzzzzz",),
            owns=("src/child/",),
        )
        _inject(empty_loop, short)
        _inject(empty_loop, long)
        _inject(empty_loop, child)

        report = empty_loop.tick()

        started = [t.packet_id for t in report.transitions if t.to_state == "running"]
        assert started == ["wp-zzzzzz"]
        assert empty_loop.packet(PacketId("wp-aaaaaa")).state == "ready"


# ════════════════════════════════════════════════════════════════
#  Tests: running → review（需要 Mock WorkerRuntime）
# ════════════════════════════════════════════════════════════════

class _MockWorkerRuntime(WorkerRuntime):
    """Mock WorkerRuntime —— spawn 立即返回，settle 返回预设结果。

    ★ 显式继承 Protocol，而不是靠结构化匹配「碰巧像」。
      结构化匹配的代价是：协议以后加一个方法，这个替身**不会有任何反应** ——
      它只是悄悄地不再是 WorkerRuntime，而测试照样绿。
      显式继承之后，协议变了这里立刻红。
      （实测：补之前它就少了 events / resume / adopt 三个方法。）
    """

    def __init__(self, outcome: WorkerOutcome | None = None) -> None:
        self._outcome = outcome or WorkerCompleted(
            evidence=(),
            spent_cny=0.5,
            touched_paths=("src/app/main.py",),
        )
        self._handles: dict[str, WorkerHandle] = {}
        self.spawn_calls: list[SpawnRequest] = []

    async def spawn(self, req: SpawnRequest) -> WorkerHandle:
        self.spawn_calls.append(req)
        h = WorkerHandle(
            worker_id=f"wrk-{req.packet_id}",
            packet_id=req.packet_id,
            role=req.role,
            runtime_ref="mock",
        )
        self._handles[req.packet_id] = h
        return h

    def events(self, handle: WorkerHandle, since_seq: int = 0) -> AsyncIterator[WorkerEvent]:
        """★ 协议要求的第三个方法。以前没实现 —— 于是这个 mock 在类型上
        根本不是 WorkerRuntime，只是「碰巧有两个同名方法的对象」。
        补上它不是为了让 mypy 闭嘴：少一个方法就意味着**这条链路从没被这个
        替身覆盖过**，而测试看起来一直是绿的。"""

        raise NotImplementedError("reconcile 目前不消费事件流；真要用时这里会明确炸")

    async def settle(self, handle: WorkerHandle) -> WorkerOutcome:
        return self._outcome

    async def abort(self, handle: WorkerHandle, reason: AbortReason) -> None:
        pass

    async def resume(self, ref: CheckpointRef) -> WorkerHandle:
        raise NotImplementedError("reconcile 目前不走恢复路径；真要用时这里会明确炸")

    async def adopt(self, runtime_ref: str) -> WorkerHandle | None:
        return None


def _completed_outcome() -> WorkerCompleted:
    """一个**真的干完了活**的 worker：带着自己产出的证据回来。

    ★ 别把 evidence 写成 ()。在 I6 下「完成但没有证据」等于没做过
      （Evidence 的定义原文：执行完成但证据没落盘 = 没做过），
      验收环节本就应该拒绝它。那个行为由
      test_completed_without_evidence_not_accepted 单独断言，
      不该混进全链路用例、让全链路绿灯建立在兜底漏洞上。
    """
    return WorkerCompleted(
        evidence=(EvidenceRef("diff:src/test/@sha256:abc123"),),
        spent_cny=0.5,
        touched_paths=("src/test/",),
    )


def _completed_outcome_no_evidence() -> WorkerCompleted:
    """自称完成、但一条证据都没落盘的 worker。"""
    return WorkerCompleted(
        evidence=(),
        spent_cny=0.5,
        touched_paths=("src/test/",),
    )


def _failed_outcome() -> WorkerFailed:
    return WorkerFailed(
        reason_code=FailureCode.RUNTIME_ERROR,
        detail="子进程非零退出",
        evidence=(),
        spent_cny=0.3,
    )


class TestRunningToReview:
    def test_worker_completed_transitions_to_review(self, empty_loop: ReconcileLoop) -> None:
        """Worker 完成后推进到 review。"""
        mock = _MockWorkerRuntime(outcome=_completed_outcome())
        empty_loop.worker_runtime = mock

        pkt = _make_packet("wp-000006", state="ready", owns=("src/test/",))
        _inject(empty_loop, pkt)

        # Tick 1: ready → running（spawn worker）
        r1 = empty_loop.tick()
        assert empty_loop.packet(PacketId("wp-000006")).state == "running"
        assert len(mock.spawn_calls) == 1

        # Tick 2: running → review（settle returns completed）
        r2 = empty_loop.tick()
        t = [x for x in r2.transitions if x.packet_id == "wp-000006"][0]
        assert t.to_state == "review"
        # 锁应该已经释放
        assert empty_loop._lock_table.holder_of("src/test/") is None

    def test_worker_failed_transitions_to_review(self, empty_loop: ReconcileLoop) -> None:
        """Worker 失败也推进到 review（让 Reviewer 判定）。"""
        failed = WorkerFailed(
            reason_code=FailureCode.ACCEPTANCE_NOT_MET,
            detail="test failed",
            evidence=(),
            spent_cny=0.3,
        )
        mock = _MockWorkerRuntime(outcome=failed)
        empty_loop.worker_runtime = mock

        pkt = _make_packet("wp-000007", state="ready", owns=("src/fail/",))
        _inject(empty_loop, pkt)

        empty_loop.tick()  # ready → running
        r2 = empty_loop.tick()  # running → review
        t = [x for x in r2.transitions if x.packet_id == "wp-000007"][0]
        assert t.to_state == "review"

    def test_without_worker_skips_running(self, empty_loop: ReconcileLoop) -> None:
        """没有 WorkerRuntime 时，running packet 保持 running。"""
        pkt = _make_packet("wp-000008", state="running", owns=("src/none/",))
        _inject(empty_loop, pkt)
        # 手动注入锁
        empty_loop._lock_table.acquire(
            PacketId("wp-000008"), ("src/none/",), at="2026-08-05T00:00:00Z"
        )

        report = empty_loop.tick()
        assert empty_loop.packet(PacketId("wp-000008")).state == "running"

    def test_review_wip_limit_delays_worker_settle(
        self, empty_loop: ReconcileLoop, tmp_state_dir: Path
    ) -> None:
        """review 列满时，running worker 先不 settle，防止评审在制品继续堆积。"""
        _write_scheduling(tmp_state_dir, running=4, review=1)
        mock = _MockWorkerRuntime(outcome=_completed_outcome())
        empty_loop.worker_runtime = mock

        running = _make_packet("wp-aaa001", state="ready", owns=("src/running/",))
        occupied_review = _make_packet("wp-zzz001", state="review", owns=("src/review/",))
        _inject(empty_loop, running)
        _inject(empty_loop, occupied_review)

        empty_loop.tick()
        assert empty_loop.packet(PacketId("wp-aaa001")).state == "running"

        report = empty_loop.tick()
        transitions_for_running = [
            t for t in report.transitions if t.packet_id == "wp-aaa001"
        ]
        assert transitions_for_running == []
        assert empty_loop.packet(PacketId("wp-aaa001")).state == "running"
        assert empty_loop.packet(PacketId("wp-zzz001")).state == "review"


# ════════════════════════════════════════════════════════════════
#  Tests: review → accepted
# ════════════════════════════════════════════════════════════════

class TestReviewToAccepted:
    def test_review_with_evidence_accepted(self, empty_loop: ReconcileLoop) -> None:
        """有证据的 review packet 推进到 accepted。"""
        pkt = _make_packet("wp-000009", state="review")
        # 手动设上证据
        pkt = pkt.model_copy(update={"evidence": ("ev-001",)})
        _inject(empty_loop, pkt)

        report = empty_loop.tick()
        t = [x for x in report.transitions if x.packet_id == "wp-000009"][0]
        assert t.to_state == "accepted"

    def test_review_without_evidence_stays(self, empty_loop: ReconcileLoop) -> None:
        """没有证据的 review packet 不能 accepted。"""
        pkt = _make_packet("wp-000010", state="review")
        _inject(empty_loop, pkt)

        report = empty_loop.tick()
        assert empty_loop.packet(PacketId("wp-000010")).state == "review"

    def test_sys_evidence_alone_not_accepted(self, empty_loop: ReconcileLoop) -> None:
        """★ 只有控制面自产的 sys: 簿记证据 → 不能验收。

        `sys:lock:` 是 _try_ready_to_running 自己写的「我拿到锁了」，
        拿它当验收依据等于系统自己给自己签字（I6：声明不算）。
        """
        pkt = _make_packet("wp-sys001", state="review")
        pkt = pkt.model_copy(update={"evidence": ("sys:lock:wp-sys001:1",)})
        _inject(empty_loop, pkt)

        empty_loop.tick()
        assert empty_loop.packet(PacketId("wp-sys001")).state == "review"

    def test_worker_failed_not_accepted(self, empty_loop: ReconcileLoop) -> None:
        """★ worker 失败的 packet 绝不能被兜底分支验收掉。

        回归防线：曾经失败只写在 transition 的 detail 字符串里，
        验收环节读不到，于是跑挂的活被静默地当成干完了。
        """
        mock = _MockWorkerRuntime(outcome=_failed_outcome())
        empty_loop.worker_runtime = mock

        pkt = _make_packet("wp-fail01", state="pending", owns=("src/fail/",))
        _inject(empty_loop, pkt)

        report = empty_loop.run_until_stable(max_ticks=20)
        final = empty_loop.packet(PacketId("wp-fail01"))

        assert final.state == "review", (
            f"worker 失败却走到了 {final.state}。"
            f"转换轨迹：{[(t.from_state, t.to_state, t.detail) for t in report.transitions]}"
        )
        assert any(
            ref.startswith("sys:worker-failed:") for ref in final.evidence
        ), f"失败没有落成机器可读的证据：{final.evidence}"

    def test_completed_without_evidence_not_accepted(
        self, empty_loop: ReconcileLoop
    ) -> None:
        """★ worker 自称完成但没落任何证据 → 停在 review。

        I6 原文：执行完成但证据没落盘 = 没做过。
        """
        mock = _MockWorkerRuntime(outcome=_completed_outcome_no_evidence())
        empty_loop.worker_runtime = mock

        pkt = _make_packet("wp-noev01", state="pending", owns=("src/noev/",))
        _inject(empty_loop, pkt)

        empty_loop.run_until_stable(max_ticks=20)
        assert empty_loop.packet(PacketId("wp-noev01")).state == "review"


# ════════════════════════════════════════════════════════════════
#  Tests: blocked → ready
# ════════════════════════════════════════════════════════════════

class TestBlockedToReady:
    def test_unblock_when_dep_satisfied(self, empty_loop: ReconcileLoop) -> None:
        """依赖满足后 blocked → ready。"""
        dep = _make_packet("wp-d4d4d4", state="accepted", owns=("src/dep4/",))
        pkt = _make_packet("wp-000011", state="blocked", deps=("wp-d4d4d4",))
        _inject(empty_loop, dep)
        _inject(empty_loop, pkt)

        report = empty_loop.tick()
        t = [x for x in report.transitions if x.packet_id == "wp-000011"][0]
        assert t.to_state == "ready"

    def test_stays_blocked_if_dep_not_ready(self, empty_loop: ReconcileLoop) -> None:
        """依赖未满足时保持 blocked。"""
        dep = _make_packet("wp-d5d5d5", state="pending", owns=("src/dep5/",))
        pkt = _make_packet("wp-000012", state="blocked", deps=("wp-d5d5d5",))
        _inject(empty_loop, dep)
        _inject(empty_loop, pkt)

        report = empty_loop.tick()
        assert empty_loop.packet(PacketId("wp-000012")).state == "blocked"


# ════════════════════════════════════════════════════════════════
#  Tests: 幂等性
# ════════════════════════════════════════════════════════════════

class TestIdempotency:
    def test_all_accepted_idempotent(self, empty_loop: ReconcileLoop) -> None:
        """全部已 accepted → tick 不应再有任何变更。"""
        pkt = _make_packet("wp-done01", state="accepted")
        _inject(empty_loop, pkt)

        r1 = empty_loop.tick()
        assert len(r1.transitions) == 0

        r2 = empty_loop.tick()
        assert len(r2.transitions) == 0

    def test_same_pending_twice_same_result(self, empty_loop: ReconcileLoop) -> None:
        """无依赖 pending → 第一次 tick 推进，第二次无变更。"""
        pkt = _make_packet("wp-000013", state="pending")
        _inject(empty_loop, pkt)

        r1 = empty_loop.tick()
        assert len(r1.transitions) == 1
        assert r1.transitions[0].to_state == "ready"

        # 第二次 tick：ready → running
        r2 = empty_loop.tick()
        running_t = [t for t in r2.transitions if t.packet_id == "wp-000013"]
        # 应该有一条（ready→running）
        assert len(running_t) <= 1


# ════════════════════════════════════════════════════════════════
#  Tests: 端到端（full lifecycle）
# ════════════════════════════════════════════════════════════════

class TestEndToEnd:
    def test_pending_to_accepted_no_deps_no_worker(self, empty_loop: ReconcileLoop) -> None:
        """无依赖、无 Worker：pending → ready → running → 然后停在 running
        （因为没有 WorkerRuntime 完成执行）。"""
        pkt = _make_packet("wp-e2e001", state="pending", owns=("src/e2e/",))
        _inject(empty_loop, pkt)

        report = empty_loop.run_until_stable(max_ticks=10)
        transitions = {t.packet_id: t for t in report.transitions if t.packet_id == "wp-e2e001"}

        final_state = empty_loop.packet(PacketId("wp-e2e001")).state
        # 无 worker：从 pending → ready → running，停在 running
        assert final_state in ("running", "review")

        # 验证状态是正向推进的
        states_seen = [
            t.to_state
            for t in report.transitions
            if t.packet_id == "wp-e2e001"
        ]
        assert "ready" in states_seen

    def test_pending_to_accepted_with_worker(self, empty_loop: ReconcileLoop) -> None:
        """全程：pending → ready → running → review → accepted（含 mock worker）。"""
        mock = _MockWorkerRuntime(outcome=_completed_outcome())
        empty_loop.worker_runtime = mock

        pkt = _make_packet("wp-e2e002", state="pending", owns=("src/e2e2/",))
        _inject(empty_loop, pkt)

        report = empty_loop.run_until_stable(max_ticks=20)

        final_state = empty_loop.packet(PacketId("wp-e2e002")).state
        assert final_state == "accepted", (
            f"Expected accepted, got {final_state}. Transitions: "
            f"{[(t.from_state, t.to_state, t.detail) for t in report.transitions]}"
        )

    def test_dep_chain_resolves_in_order(self, empty_loop: ReconcileLoop) -> None:
        """链式依赖按序推进：A(accepted) → B(pending, deps=A) → C(pending, deps=B)。"""
        mock = _MockWorkerRuntime(outcome=_completed_outcome())
        empty_loop.worker_runtime = mock

        a = _make_packet("wp-aaaaa1", state="accepted", owns=("src/A/",))
        b = _make_packet("wp-bbbbb1", state="pending", owns=("src/B/",), deps=("wp-aaaaa1",))
        c = _make_packet("wp-cccc01", state="pending", owns=("src/C/",), deps=("wp-bbbbb1",))
        _inject(empty_loop, a)
        _inject(empty_loop, b)
        _inject(empty_loop, c)

        report = empty_loop.run_until_stable(max_ticks=30)

        # B 应该已经 accepted
        assert empty_loop.packet(PacketId("wp-bbbbb1")).state == "accepted"
        # C 应该已经 accepted（因为 B 是它的依赖）
        assert empty_loop.packet(PacketId("wp-cccc01")).state == "accepted"


# ════════════════════════════════════════════════════════════════
#  Tests: 持久化（save/load 往返）
# ════════════════════════════════════════════════════════════════

class TestPersistence:
    def test_save_load_roundtrip(self, tmp_state_dir: Path) -> None:
        """save → load → 状态一致。"""
        loop1 = ReconcileLoop(state_dir=str(tmp_state_dir))
        pkt = _make_packet("wp-rt0001", state="pending")
        _inject(loop1, pkt)

        # 推进并保存
        loop1.tick()
        loop1.save_state()

        # 重新加载
        loop2 = ReconcileLoop(state_dir=str(tmp_state_dir))
        loop2.load_state()

        assert loop2.packet_count == 1
        assert loop2.packet(PacketId("wp-rt0001")).state == "ready"


# ════════════════════════════════════════════════════════════════
#  Tests: 边界条件
# ════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_terminal_states_ignored(self, empty_loop: ReconcileLoop) -> None:
        """accepted / abandoned 状态的 packet 不被 reconcile 触碰。"""
        acc = _make_packet("wp-term01", state="accepted")
        abn = _make_packet("wp-term02", state="abandoned")
        _inject(empty_loop, acc)
        _inject(empty_loop, abn)

        report = empty_loop.tick()
        assert len(report.transitions) == 0

    def test_empty_state_dir_no_crash(self, tmp_state_dir: Path) -> None:
        """空 state_dir 的 reconcile 不应崩溃。"""
        loop = ReconcileLoop(state_dir=str(tmp_state_dir))
        loop.load_state()
        assert loop.packet_count == 0
        report = loop.tick()
        assert len(report.transitions) == 0

    def test_tick_report_has_transitions_and_errors(self, empty_loop: ReconcileLoop) -> None:
        """TickReport 正确记录转换和错误。"""
        pkt = _make_packet("wp-000014", state="pending")
        _inject(empty_loop, pkt)

        report = empty_loop.tick()
        assert isinstance(report, TickReport)
        assert len(report.transitions) > 0
        assert isinstance(report.transitions[0], PacketTransition)


# ════════════════════════════════════════════════════════════════
#  Tests: 「说完成」不等于「干了活」 —— 缺陷二的另一半
# ════════════════════════════════════════════════════════════════

class TestCompletedButTouchedNothing:
    """★ 回归防线：worker 自称完成、证据也是真的，但一个文件都没改。

    2026-08-10 修掉的是「拿控制面自己的簿记当证据」，
    B 在 runner 里修掉的是「模型明说 blocker 却判 completed」。
    剩下这一半最安静：**模型什么都没说，只是什么也没做** ——
    把代码写在回复正文里，`result.json` 确实落了盘（所以证据是真的），
    但工作区一个文件都没变。

    ★ 判据一直都在：`WorkerCompleted.touched_paths` 是**冻结契约里的字段**，
      worker 老老实实报了。控制平面从来没看过一眼 ——
      不是缺数据，是缺判定。
    """

    def test_impl_packet_that_touched_nothing_is_not_accepted(
        self, empty_loop: ReconcileLoop
    ) -> None:
        """带着真实证据、但 touched_paths 为空 → 停在 review，不得验收。"""

        mock = _MockWorkerRuntime(
            outcome=WorkerCompleted(
                # ★ 证据是**真的**（非 sys: 前缀）—— 这正是这条缺陷难抓的原因：
                #   前两次的判据（前缀判定）在这里全部放行。
                evidence=(EvidenceRef("file:model/result.json"),),
                spent_cny=0.5,
                touched_paths=(),
            )
        )
        empty_loop.worker_runtime = mock

        pkt = _make_packet("wp-notouch01", state="pending", owns=("src/notouch/",))
        _inject(empty_loop, pkt)

        report = empty_loop.run_until_stable(max_ticks=20)
        final = empty_loop.packet(PacketId("wp-notouch01"))

        assert final.state == "review", (
            f"一个文件都没改却走到了 {final.state} —— 「写了字」被当成了「交了活」。"
            f"轨迹：{[(t.from_state, t.to_state, t.detail) for t in report.transitions]}"
        )
        assert any(ref.startswith("sys:worker-failed:") for ref in final.evidence), (
            f"没落成机器可读的失败标记，下游读不到：{final.evidence}"
        )

    def test_impl_packet_that_touched_files_is_accepted(
        self, empty_loop: ReconcileLoop
    ) -> None:
        """★ 对照组。没有它，上面那条用「永远停在 review」也能绿。"""

        empty_loop.worker_runtime = _MockWorkerRuntime(outcome=_completed_outcome())

        pkt = _make_packet("wp-touch0001", state="pending", owns=("src/test/",))
        _inject(empty_loop, pkt)

        empty_loop.run_until_stable(max_ticks=20)
        assert empty_loop.packet(PacketId("wp-touch0001")).state == "accepted"

    def test_kinds_that_legitimately_touch_nothing_are_not_punished(
        self, empty_loop: ReconcileLoop
    ) -> None:
        """★ 第二个对照组：`review` 类 packet 的产出是判断，不是文件。

        一刀切「没改文件就算没干活」会把它们全判失败 ——
        那不是更严格，是把判据变成噪音。
        """

        empty_loop.worker_runtime = _MockWorkerRuntime(
            outcome=WorkerCompleted(
                evidence=(EvidenceRef("review:wp-review001@sha256:abc"),),
                spent_cny=0.2,
                touched_paths=(),
            )
        )

        pkt = _make_packet(
            "wp-review001", state="pending", owns=("src/review/",), kind="review"
        )
        _inject(empty_loop, pkt)

        empty_loop.run_until_stable(max_ticks=20)
        assert empty_loop.packet(PacketId("wp-review001")).state == "accepted", (
            "review 类 packet 本来就可能一个文件都不碰，不该被这条判据误伤"
        )


class TestBookkeepingPathsDoNotCountAsWork:
    """★★ 第二次踩同一个坑：`touched_paths` 本身会被系统自己的产物污染。

    2026-08-11 真链路实测：模型一个文件都没建，`touched_paths` 却有 10 条 ——
    全是 `.codentum/evidence/**`，harness 自己写进 worker 工作区的
    prompt / response / usage / manifest。

    于是「改动数 > 0」这条判据**被系统自己的产物满足了**。
    这和「拿 sys: 簿记当证据」是同一个 bug 下沉了一层：
    前者污染 evidence 列表，这里污染 touched_paths。

    ★ 单元测试当时全绿 —— 因为 mock 里的 touched_paths 是干净的。
      只有真的跑一次链路才看得见。这条测试把那次实测钉下来。
    """

    def test_only_bookkeeping_touched_is_not_work(self, empty_loop: ReconcileLoop) -> None:
        """全是 `.codentum/` 下的文件 → 等于什么都没干。"""

        empty_loop.worker_runtime = _MockWorkerRuntime(
            outcome=WorkerCompleted(
                evidence=(EvidenceRef("file:model/result.json"),),
                spent_cny=0.5,
                touched_paths=(
                    ".codentum/evidence/wp-bk01-attempt-1/model/result.json",
                    ".codentum/evidence/wp-bk01-attempt-1/prompt/user.md",
                    ".codentum/evidence/wp-bk01-attempt-1/events.jsonl",
                ),
            )
        )
        pkt = _make_packet("wp-bk000001", state="pending", owns=("src/bk/",))
        _inject(empty_loop, pkt)

        empty_loop.run_until_stable(max_ticks=20)
        final = empty_loop.packet(PacketId("wp-bk000001"))
        assert final.state == "review", (
            f"系统自己写的簿记被当成了「干了活」，packet 走到了 {final.state}"
        )

    def test_windows_separators_are_normalized(self, empty_loop: ReconcileLoop) -> None:
        """★ 反斜杠也必须被识别为簿记。

        漏掉归一化的话，判据在 Linux 上有效、在 Windows 上失效，**且不报错**。
        本项目已经踩过两次同类（EvidenceRef 分隔符、流编码）。
        """

        empty_loop.worker_runtime = _MockWorkerRuntime(
            outcome=WorkerCompleted(
                evidence=(EvidenceRef("file:model/result.json"),),
                spent_cny=0.5,
                touched_paths=(r".codentum\evidence\wp-bk02-attempt-1\model\result.json",),
            )
        )
        pkt = _make_packet("wp-bk000002", state="pending", owns=("src/bk2/",))
        _inject(empty_loop, pkt)

        empty_loop.run_until_stable(max_ticks=20)
        assert empty_loop.packet(PacketId("wp-bk000002")).state == "review"

    def test_real_output_alongside_bookkeeping_still_counts(
        self, empty_loop: ReconcileLoop
    ) -> None:
        """★ 对照组：簿记之外还有真产出 → 必须照常验收。

        没有这条，上面两条用「一律判未完成」也能绿。
        """

        empty_loop.worker_runtime = _MockWorkerRuntime(
            outcome=WorkerCompleted(
                evidence=(EvidenceRef("file:model/result.json"),),
                spent_cny=0.5,
                touched_paths=(
                    ".codentum/evidence/wp-bk03-attempt-1/model/result.json",
                    "src/bk3/subscriptions.py",  # ← 真正的产出
                ),
            )
        )
        pkt = _make_packet("wp-bk000003", state="pending", owns=("src/bk3/",))
        _inject(empty_loop, pkt)

        empty_loop.run_until_stable(max_ticks=20)
        assert empty_loop.packet(PacketId("wp-bk000003")).state == "accepted"


# ══════════════════════════════════════════════════════════════
#  状态目录自愈
# ══════════════════════════════════════════════════════════════


class TestStateDirSelfHeals:
    """★ 状态文件在**运行中**消失时，下一轮 tick 要自己补回来。

    2026-08-12 实际发生过：另一个进程把 `.codentum/` 当调试残留删了，
    运行中的桌面端立刻排出六条 `[missing]`，而引擎毫无察觉 ——
    照常 tick、照常写 packet，写进一个已经不存在的目录，
    **直到有人重启它才恢复**。

    `ensure_state_dir()` 原来只在启动时跑一次，这就是缺口。
    """

    def test_deleted_state_files_come_back_on_next_tick(self, tmp_path: Path) -> None:
        state_dir = tmp_path / ".codentum"
        loop = ReconcileLoop(state_dir=str(state_dir))
        loop.ensure_state_dir()

        # 模拟「别的进程把状态删了」
        shutil.rmtree(state_dir)
        assert not state_dir.exists()

        loop.tick()

        assert (state_dir / "graph.json").is_file(), "graph.json 没有被补回来"
        assert (state_dir / "scheduling.json").is_file()
        assert (state_dir / "decisions.jsonl").is_file()
        assert (state_dir / "packets").is_dir()
        assert (state_dir / "evidence").is_dir()

    def test_partial_deletion_is_healed_too(self, tmp_path: Path) -> None:
        """★ 半个状态目录比没有状态目录更坏 —— 桌面端会显示一串错误
        而不是「尚未初始化」。所以部分缺失也要补。"""

        state_dir = tmp_path / ".codentum"
        loop = ReconcileLoop(state_dir=str(state_dir))
        loop.ensure_state_dir()

        (state_dir / "graph.json").unlink()
        shutil.rmtree(state_dir / "packets")

        loop.tick()

        assert (state_dir / "graph.json").is_file()
        assert (state_dir / "scheduling.json").is_file()
        assert (state_dir / "packets").is_dir()

    def test_healing_does_not_overwrite_surviving_state(self, tmp_path: Path) -> None:
        """★ 自愈只补缺，**绝不覆盖**。

        补的时候顺手把幸存的 packet 冲掉，比不自愈严重得多 ——
        前者丢数据，后者只是显示异常。
        """

        state_dir = tmp_path / ".codentum"
        loop = ReconcileLoop(state_dir=str(state_dir))
        loop.ensure_state_dir()

        survivor = state_dir / "packets" / "wp-survivor01.json"
        survivor.write_text('{"id": "wp-survivor01"}', encoding="utf-8")
        (state_dir / "decisions.jsonl").write_text('{"kept": true}\n', encoding="utf-8")
        (state_dir / "graph.json").unlink()  # 只删这一个

        loop.tick()

        assert survivor.read_text(encoding="utf-8") == '{"id": "wp-survivor01"}'
        assert (state_dir / "decisions.jsonl").read_text(encoding="utf-8") == '{"kept": true}\n'
        assert (state_dir / "graph.json").is_file()

    def test_ensure_state_dir_writes_scheduling_projection(self, tmp_path: Path) -> None:
        """C 读 scheduling.json，不自行推算 WIP、容量和队列。"""

        state_dir = tmp_path / ".codentum"
        loop = ReconcileLoop(state_dir=str(state_dir))

        loop.ensure_state_dir()

        projection = json.loads((state_dir / "scheduling.json").read_text(encoding="utf-8"))
        assert projection["schemaVersion"] == 1
        assert projection["wipLimits"] == {"review": 2, "running": 4}
        assert projection["current"]["running"] == 0
        assert projection["capacity"] == {"review": 2, "running": 4}
        assert projection["readyQueue"] == []
        assert projection["policy"]["ordering"] == "critical_path_desc_then_packet_id"

    def test_healing_is_reported_not_silent(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """★ 自愈必须**出声**。

        静默自愈会把「有人在删你的状态」这件事藏起来 —— 而那是个真问题。
        自愈让系统能继续跑，出声让人知道刚才发生过异常。**两者都要。**
        """

        state_dir = tmp_path / ".codentum"
        loop = ReconcileLoop(state_dir=str(state_dir))
        loop.ensure_state_dir()
        shutil.rmtree(state_dir)

        with caplog.at_level(logging.WARNING):
            loop.tick()

        assert any("已自动补齐" in record.message for record in caplog.records), (
            "状态被补回来了，但没有任何日志 —— 下次再发生就查不到了"
        )

    def test_steady_state_stays_quiet(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """★ 对照组：什么都没缺时不该有任何 warning。

        没有它，上面那条用「每轮都 warning」也能绿 ——
        而那会让日志里全是噪音，真出事时反而看不见。
        """

        state_dir = tmp_path / ".codentum"
        loop = ReconcileLoop(state_dir=str(state_dir))
        loop.ensure_state_dir()

        with caplog.at_level(logging.WARNING):
            loop.tick()
            loop.tick()

        assert not [r for r in caplog.records if "已自动补齐" in r.message]


class TestIncrementalPersistence:
    """★ 每个 transition 立刻落盘，不等整轮结束。

    `_try_running_to_review` 里的 settle() 是阻塞的：多个 running packet
    在同一轮里顺序 settle，若落盘在整轮之后，磁盘状态会停在最慢那个
    worker 的进度上。

    2026-08-13 实测：两个 worker 已失败 8 分钟，磁盘上仍是 8 个 running。
    """

    def test_state_is_written_before_the_tick_finishes(self, tmp_path: Path) -> None:
        state_dir = tmp_path / ".codentum"
        loop = ReconcileLoop(state_dir=str(state_dir))
        loop.ensure_state_dir()

        # 两个无依赖的 packet：第一个推进后、处理第二个时，磁盘上就该能看到第一个
        seen_during_tick: list[str] = []

        original = loop.save_state

        def spy() -> None:
            original()
            packets_dir = state_dir / "packets"
            states = []
            for path in sorted(packets_dir.glob("*.json")):
                states.append(json.loads(path.read_text(encoding="utf-8"))["state"])
            seen_during_tick.append(",".join(states))

        loop.save_state = spy  # type: ignore[method-assign]

        for index in range(2):
            _inject(loop, _make_packet(f"wp-inc{index:08d}", owns=(f"workspace/m{index}/",)))
        original()
        seen_during_tick.clear()

        loop.tick()

        # ★ 关键：tick 过程中至少落盘过 2 次（每个 transition 一次），
        #   而不是只在最后落一次
        assert len(seen_during_tick) >= 2, (
            f"整轮只落盘了 {len(seen_during_tick)} 次 —— 慢 worker 会让状态长时间不可见"
        )


# ════════════════════════════════════════════════════════════════
#  决策日志：flow.json 要算的那段历史
#
#  ★ `DecisionRecord` 的 schema 冻结于 2026-08-02、被 contracts 导出 ——
#    但**全仓库没有任何一处构造过它**。`decisions.jsonl` 被
#    ensure_state_dir() 建成空文件，然后一直是空的。
#
#    后果不是「少一个日志」：**每个 packet 在每个状态停了多久，
#    这段历史根本不存在**。流动效率、等待 p80、瓶颈都算不出来 ——
#    不是算法没写，是没有可算的东西。
# ════════════════════════════════════════════════════════════════


class TestDecisionLog:
    def _lines(self, state_dir: Path) -> list[dict[str, object]]:
        path = state_dir / "decisions.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def test_every_transition_is_recorded(self, empty_loop: ReconcileLoop, tmp_state_dir: Path) -> None:
        """★ 挂在 `_apply_transition` 上，因为那是 reconcile **唯一**修改状态的地方。

        挂在别处必然漏 —— 而漏掉的那几条转移会让 flow 的时长凭空少一段，
        且没有任何东西会报错。
        """

        _inject(empty_loop, _make_packet("wp-dec00001", state="pending"))
        empty_loop.tick()

        rows = self._lines(tmp_state_dir)
        assert rows, "转移发生了，decisions.jsonl 仍是空的"
        assert all(row["action"] == "packet_transitioned" for row in rows)

    def test_records_conform_to_the_frozen_contract(
        self, empty_loop: ReconcileLoop, tmp_state_dir: Path
    ) -> None:
        """★ 用**契约模型**回读，而不是自己断言几个字段。

        自己写断言只能覆盖想到的字段；用冻结的模型解析，
        任何不合规（缺字段、reasonCode 带空格或中文）都会被拒。
        """

        from codentum_contracts.state import DecisionRecord

        _inject(empty_loop, _make_packet("wp-dec00002", state="pending"))
        empty_loop.tick()

        for row in self._lines(tmp_state_dir):
            record = DecisionRecord.model_validate(row)
            assert record.packetId == "wp-dec00002"
            assert record.at, "没有时间戳 —— flow 的全部计算都靠它"
            # reasonCode 必须机器可读：控制平面据此决策，桌面端据此分类
            assert record.reasonCode.startswith("Reconcile.")

    def test_log_is_append_only(self, empty_loop: ReconcileLoop, tmp_state_dir: Path) -> None:
        """★ 只追加不改写 —— 覆盖会把历史抹掉，而历史正是要算的东西。"""

        _inject(empty_loop, _make_packet("wp-dec00003", state="pending"))
        empty_loop.tick()
        first = len(self._lines(tmp_state_dir))

        _inject(empty_loop, _make_packet("wp-dec00004", state="pending", owns=("src/other/",)))
        empty_loop.tick()

        assert len(self._lines(tmp_state_dir)) > first, "第二轮把第一轮覆盖了"

    def test_transition_survives_a_broken_log(
        self, empty_loop: ReconcileLoop, tmp_state_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """★ 记账失败不能拖垮状态推进 —— 但也不能静默。

        一个因为磁盘写不进去就卡住整条流水线的日志，比没有日志更糟。
        """

        real_open = Path.open

        def boom(self: Path, *args: Any, **kwargs: Any) -> Any:
            # ★ 只拦「决策日志的**追加**」。范围窄一格再窄一格是有原因的：
            #   打掉整个 Path.open 会连 packet 落盘一起坏（那测的是磁盘坏了）；
            #   只按文件名拦又会打到 ensure_state_dir() 建空文件那一步（'w' 模式），
            #   而那发生在转移**之前** —— 测的就变成「目录自愈坏了」。
            #   要测的是「日志追加失败时状态推进照常」，所以只拦 'a'。
            if self.name == "decisions.jsonl" and "a" in str(kwargs.get("mode", args[0] if args else "")):
                raise OSError("disk full")
            return real_open(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", boom)
        _inject(empty_loop, _make_packet("wp-dec00005", state="pending"))
        report = empty_loop.tick()

        assert report.transitions, "日志写不进去，状态推进也停了"


# ════════════════════════════════════════════════════════════════
#  产出合入：「accepted」必须意味着东西真的进了项目
# ════════════════════════════════════════════════════════════════


class TestResultIntegration:
    def _reviewable(self, loop: ReconcileLoop, pid: str) -> None:
        packet = _make_packet(pid, state="review").model_copy(
            update={"evidence": (EvidenceRef("file:model/result.json"),), "attempts": 1}
        )
        _inject(loop, packet)

    def test_integration_failure_blocks_instead_of_looping(
        self, empty_loop: ReconcileLoop
    ) -> None:
        """★ 合入失败转 blocked，不是留在 review。

        留在 review 会每轮重试合并，而失败原因（工作区不干净、改了别人的
        路径、合并冲突）通常不会自己消失 —— 那只是把一次失败变成无限次失败。
        """

        empty_loop.result_integrator = lambda _p: (False, "工作区有未提交的改动")
        self._reviewable(empty_loop, "wp-integ0001")
        empty_loop.tick()

        packet = empty_loop.packets[PacketId("wp-integ0001")]
        assert packet.state == "blocked"

    def test_accepted_detail_says_whether_anything_was_merged(
        self, empty_loop: ReconcileLoop
    ) -> None:
        """★ 「合入了 3 处」与「压根没合」在状态上**都是 accepted**。

        不把结论写进 detail 的话，这两件事从外面完全分辨不出来 ——
        而那正是这条缺口原本的样子：验收通过只是一句状态字符串。
        """

        empty_loop.result_integrator = lambda _p: (True, "**未合入**：找不到工作区")
        self._reviewable(empty_loop, "wp-integ0002")
        report = empty_loop.tick()

        (transition,) = [t for t in report.transitions if t.to_state == "accepted"]
        assert "未合入" in transition.detail, "accepted 了，却看不出到底合没合"

    def test_no_integrator_keeps_the_old_behaviour(self, empty_loop: ReconcileLoop) -> None:
        """没注入合入器时保持原样 —— 控制平面不自己做 git。"""

        assert empty_loop.result_integrator is None
        self._reviewable(empty_loop, "wp-integ0003")
        empty_loop.tick()
        assert empty_loop.packets[PacketId("wp-integ0003")].state == "accepted"
