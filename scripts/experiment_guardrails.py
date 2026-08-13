#!/usr/bin/env python3
"""护栏消融实验 —— 产出可复现的对照数据。

════════════════════════════════════════════════════════════════
 这个实验回答什么问题
════════════════════════════════════════════════════════════════

Codentum 的中心主张是「可靠性来自不变量，不来自提示词」。
这句话要么是可测量的，要么是修辞。**这个脚本把它变成可测量的。**

做法是消融（ablation）：把同一批**注入了已知故障**的 WorkPacket，
分别在「护栏开」与「护栏关」两种配置下跑完整的 ReconcileLoop，
数一数有多少故障被静默放行。

★ 为什么「护栏关」不是人造的稻草人：
  `ReconcileLoop` 的四个安全组件（transition_table / gate_runner /
  budget_tracker / guardian）**默认值全是 None**。
  也就是说，「护栏关」就是这套系统的**默认配置**，不是为了做实验特意拆的。
  这一条本身就是实验结论的一部分。

════════════════════════════════════════════════════════════════
 三组测量
════════════════════════════════════════════════════════════════

M1 护栏消融      三个强制点 × {开, 关}，每个注入 8 个形状不同的故障
M2 门禁修复前后  同一注入下，08-10 修复前的门禁 vs 修复后的门禁
M3 路径锁压测    并发抢重叠路径，有锁 vs 无锁，统计真实冲突次数

M1/M2 是确定性的（同输入必同输出）；M3 是统计性的（真实线程竞争）。
两类都要有 —— 只有确定性数据，图上全是 0 和 100%，看不出量级；
只有统计数据，又证明不了「哪条不变量在起作用」。

════════════════════════════════════════════════════════════════
 用法
════════════════════════════════════════════════════════════════

    python scripts/experiment_guardrails.py

产出（均可复现，不含时间戳以外的随机量）：

    docs/experiments/guardrail-ablation.json   原始数据
    docs/experiments/guardrail-ablation.svg    对照图（自绘，零依赖）

零第三方依赖 —— 与仓库里其他四个工程脚本同一条规矩：
`pip install` 之前就能跑。

owner: A
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import json
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for _root in (
    ROOT / "packages" / "contracts" / "python",
    ROOT / "packages" / "control-plane",
):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

from codentum_contracts.state import (
    Acceptance,
    BudgetGrant,
    EvidenceRef,
    PacketId,
    Provenance,
    WorkPacket,
    dump_state  # noqa: E402,
)
from codentum_control_plane.budget import BudgetTracker  # noqa: E402
from codentum_control_plane.evidence import acceptance_evidence  # noqa: E402
from codentum_control_plane.gates import (  # noqa: E402
    GateRunner,
    GateVerdict,
    register_builtin_gates,
)
from codentum_control_plane.guardian import Guardian  # noqa: E402
from codentum_control_plane.locks import LockTable  # noqa: E402
from codentum_control_plane.locks.path_lock import AcquireResult  # noqa: E402
from codentum_control_plane.reconcile import ReconcileLoop  # noqa: E402

OUT_DIR = ROOT / "docs" / "experiments"


# ════════════════════════════════════════════════════════════════
#  测试替身
# ════════════════════════════════════════════════════════════════

class _NoLockTable(LockTable):
    """把 I1 的强制点摘掉：申请永远成功。

    ★ 这不是「模拟一个坏掉的锁」，而是「没有锁」——
      即路径独占只停留在 boundaries.yaml 的约定层面，没有代码强制。
      这正是对照组要代表的世界。
    """

    def acquire(  # noqa: D102
        self,
        packet_id: PacketId,
        paths: Sequence[str],
        *,
        at: str,
        expected_version: int | None = None,
    ) -> AcquireResult:
        return AcquireResult(ok=True, version=self.version, acquired=tuple(paths))


class _UnsynchronizedLockTable(LockTable):
    """保留全部冲突检测逻辑，只把互斥锁换成空操作。

    ★ M3 的对照组必须是这个，而不是 `_NoLockTable`。
      「锁根本不存在」当然每轮都是 5 个赢家 —— 那不是竞争实验，
      是把结论写进了对照组。这里保留 check-then-act 的全部判定，
      只去掉让它原子的那把锁，测的才是「并发下判定还成不成立」。

    ⚠️ 这正是 08-03 踩过的坑的反面：当时把互斥锁换成空操作跑 400 轮，
      **依然每轮恰好一个赢家** —— 因为临界区太短，GIL 没机会切走。
      把 `sys.setswitchinterval` 压到 1µs 后才暴露。
      所以这个对照组只有配合 1µs 才有意义，见 measure_lock_race。
    """

    def __init__(self, *, version: int = 0) -> None:
        super().__init__(version=version)
        self._mutex = _NullMutex()  # type: ignore[assignment]


class _NullMutex:
    """空互斥锁。`with` 进出都不做任何事。"""

    def __enter__(self) -> None:
        return None

    def __exit__(self, *_exc: object) -> None:
        return None


def _legacy_gate_verdict(gate_id: str, packet: WorkPacket) -> GateVerdict:
    """复刻 08-10 修复**之前**的内置门禁逻辑：只判 `packet.evidence` 非空。

    ★ 原文是 `if not packet.evidence: fail`，四个门禁都一样。
      问题在于 packet 手上那条 `sys:lock:<pid>` 是控制面自己写的簿记 ——
      于是「拿到过锁」被当成了「活干完了」。
      这段代码只在本实验里存在，用于产出修复前后的对照数据。
    """
    if not packet.evidence:
        return GateVerdict(passed=False, gate_id=gate_id, detail="legacy: 无证据")
    return GateVerdict(
        passed=True,
        gate_id=gate_id,
        detail=f"legacy: {len(packet.evidence)} 条证据",
        evidence_refs=packet.evidence,
    )


def _legacy_runner() -> GateRunner:
    runner = GateRunner()
    for gid in ("evidence_exists", "self-test", "acceptance", "review"):
        runner.register(gid, lambda p, _g=gid, **_c: _legacy_gate_verdict(_g, p))
    return runner


# ════════════════════════════════════════════════════════════════
#  Packet 构造
# ════════════════════════════════════════════════════════════════

def _packet(
    pid: str,
    *,
    state: str = "pending",
    owns: tuple[str, ...] = ("src/x/",),
    attempts: int = 0,
    limit_cny: float = 5.0,
    evidence: tuple[EvidenceRef, ...] = (),
    role: str = "coder",
) -> WorkPacket:
    return WorkPacket(
        id=PacketId(pid),
        kind="impl",
        state=state,  # type: ignore[arg-type]
        role=role,  # type: ignore[arg-type]
        ownsPaths=owns,
        readsPaths=("tests/",),
        deps=(),
        acceptance=Acceptance(kind= "test", predicate= "pytest", authoredBy= "qa"),
        budget=BudgetGrant(
            currency= "CNY",
            limitCny= limit_cny,
            spentCny= 0.0,
            degradationChain= ("drop_semantic",),
        ),
        attempts=attempts,
        evidence=evidence,
        provenance=Provenance(createdBy= "planner", createdAt= "2026-08-05T00:00:00Z"),
    )


def _run(state_dir: Path, packets: list[WorkPacket], **guardrails: object) -> dict[str, Any]:
    """把一批 packet 落盘后跑到稳定，返回终态。

    ★ 刻意走磁盘而不是直接塞 `loop._packets` —— 生产路径是
      `load_state()` 从磁盘读，绕过它测的就不是同一条路。
    """
    for f in (state_dir / "packets").glob("*.json"):
        f.unlink()
    (state_dir / "packets").mkdir(parents=True, exist_ok=True)

    # ★ 也要清掉 graph.json 里的**残留锁**。
    #
    #   「护栏开」那轮会先拿锁、再被护栏拦下，锁就留在了 graph.json 里。
    #   只清 packets/ 的话，「护栏关」那轮 load_state() 会加载到这把残留锁，
    #   packet 卡在 ready 拿不到锁 —— **看起来像护栏还在生效**，
    #   于是 leaked_off 变成 0，而那正是「护栏没用」的读数。
    #
    #   ★ 这个隔离缺陷一直存在，只是此前 `_run` 从不调用 save_state()
    #     所以 graph.json 从没被写过。2026-08-13 给 tick 加了逐条落盘后
    #     立刻暴露 —— **实验的隔离性不该依赖「被测代码恰好不写盘」**。
    graph = state_dir / "graph.json"
    if graph.exists():
        graph.unlink()
    for p in packets:
        (state_dir / "packets" / f"{p.id}.json").write_text(
            json.dumps(dump_state(p), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    loop = ReconcileLoop(state_dir=str(state_dir), **guardrails)  # type: ignore[arg-type]
    loop.load_state()
    if guardrails.get("_no_lock"):  # pragma: no cover - 由调用方显式传
        loop._lock_table = _NoLockTable()
    loop.run_until_stable(max_ticks=30)
    return {str(p.id): loop.packet(p.id).state for p in packets}


def _run_with_lock_table(
    state_dir: Path, packets: list[WorkPacket], lock_table: LockTable | None
) -> dict[str, Any]:
    for f in (state_dir / "packets").glob("*.json"):
        f.unlink()
    (state_dir / "packets").mkdir(parents=True, exist_ok=True)

    # ★ 也要清掉 graph.json 里的**残留锁**。
    #
    #   「护栏开」那轮会先拿锁、再被护栏拦下，锁就留在了 graph.json 里。
    #   只清 packets/ 的话，「护栏关」那轮 load_state() 会加载到这把残留锁，
    #   packet 卡在 ready 拿不到锁 —— **看起来像护栏还在生效**，
    #   于是 leaked_off 变成 0，而那正是「护栏没用」的读数。
    #
    #   ★ 这个隔离缺陷一直存在，只是此前 `_run` 从不调用 save_state()
    #     所以 graph.json 从没被写过。2026-08-13 给 tick 加了逐条落盘后
    #     立刻暴露 —— **实验的隔离性不该依赖「被测代码恰好不写盘」**。
    graph = state_dir / "graph.json"
    if graph.exists():
        graph.unlink()
    for p in packets:
        (state_dir / "packets" / f"{p.id}.json").write_text(
            json.dumps(dump_state(p), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    loop = ReconcileLoop(state_dir=str(state_dir))
    loop.load_state()
    if lock_table is not None:
        loop._lock_table = lock_table
    loop.run_until_stable(max_ticks=30)
    return {str(p.id): loop.packet(p.id).state for p in packets}


# ════════════════════════════════════════════════════════════════
#  M1 护栏消融
# ════════════════════════════════════════════════════════════════

#: I1 的八种重叠形状。★ 刻意不是同一个 case 重复八遍 ——
#: 那样测的是「同一条路径走八次」，覆盖面没有变化。
#: 这八种全部应当被判为冲突，其中五种依赖路径归一化。
_OVERLAP_SHAPES: tuple[tuple[str, str, str], ...] = (
    ("同一路径", "src/a/", "src/a/"),
    ("尾斜杠差异", "src/b/", "src/b"),
    ("父占子", "src/c/", "src/c/deep/"),
    ("子占父", "src/d/nested/", "src/d/"),
    ("点前缀", "src/e/", "./src/e/"),
    ("重复斜杠", "src/f/", "src//f/"),
    ("反斜杠", "src/g/", "src\\g\\"),
    ("大小写", "src/h/", "SRC/H/"),
)


def measure_lock(state_dir: Path) -> dict[str, Any]:
    """I1 单写者：两个 packet 声明重叠路径，只应有一个拿到写权限。"""
    on_violations = 0
    off_violations = 0
    detail = []
    for i, (shape, pa, pb) in enumerate(_OVERLAP_SHAPES):
        pkts = [
            _packet(f"wp-lock{i:02d}a", owns=(pa,)),
            _packet(f"wp-lock{i:02d}b", owns=(pb,)),
        ]
        on = _run_with_lock_table(state_dir, pkts, None)
        off = _run_with_lock_table(state_dir, pkts, _NoLockTable())
        on_running = sum(1 for s in on.values() if s == "running")
        off_running = sum(1 for s in off.values() if s == "running")
        on_bad = on_running > 1
        off_bad = off_running > 1
        on_violations += on_bad
        off_violations += off_bad
        detail.append({
            "shape": shape, "paths": [pa, pb],
            "guardrail_on_running": on_running,
            "guardrail_off_running": off_running,
        })
    return {
        "invariant": "I1 单写者（路径锁）",
        "fault": "两个 packet 声明重叠路径",
        "violation": "两个都拿到写权限",
        "injected": len(_OVERLAP_SHAPES),
        "leaked_on": on_violations,
        "leaked_off": off_violations,
        "cases": detail,
    }


def measure_guardian(state_dir: Path) -> dict[str, Any]:
    """I5 单会话闭合：尝试次数超上限的 packet 不该再被派工。"""
    attempts = (3, 4, 5, 6, 7, 9, 12, 20)
    leaked_on = leaked_off = 0
    detail = []
    for i, n in enumerate(attempts):
        pkts = [_packet(f"wp-guard{i:02d}", owns=(f"src/gd{i}/",), attempts=n)]
        on = _run(state_dir, pkts, guardian=Guardian())
        off = _run(state_dir, pkts)
        on_bad = on[f"wp-guard{i:02d}"] == "running"
        off_bad = off[f"wp-guard{i:02d}"] == "running"
        leaked_on += on_bad
        leaked_off += off_bad
        detail.append({
            "attempts": n,
            "guardrail_on_state": on[f"wp-guard{i:02d}"],
            "guardrail_off_state": off[f"wp-guard{i:02d}"],
        })
    return {
        "invariant": "I5 单会话闭合（重试上限）",
        "fault": "packet 尝试次数已达/超过上限 3",
        "violation": "仍然被派工执行",
        "injected": len(attempts),
        "leaked_on": leaked_on,
        "leaked_off": leaked_off,
        "cases": detail,
    }


def measure_budget(state_dir: Path) -> dict[str, Any]:
    """全局预算：单个 packet 要的钱超过全局余额时不该开工。"""
    limits = (1.5, 2.0, 3.0, 5.0, 8.0, 13.0, 21.0, 34.0)
    global_limit = 1.0
    leaked_on = leaked_off = 0
    detail = []
    for i, need in enumerate(limits):
        pid = f"wp-budget{i:02d}"
        pkts = [_packet(pid, owns=(f"src/bg{i}/",), limit_cny=need)]
        on = _run(state_dir, pkts, budget_tracker=BudgetTracker(limit_cny=global_limit))
        off = _run(state_dir, pkts)
        on_bad = on[pid] == "running"
        off_bad = off[pid] == "running"
        leaked_on += on_bad
        leaked_off += off_bad
        detail.append({
            "needs_cny": need, "global_limit_cny": global_limit,
            "guardrail_on_state": on[pid],
            "guardrail_off_state": off[pid],
        })
    return {
        "invariant": "全局预算上限",
        "fault": f"packet 申领 > 全局余额 ¥{global_limit}",
        "violation": "仍然开工花钱",
        "injected": len(limits),
        "leaked_on": leaked_on,
        "leaked_off": leaked_off,
        "cases": detail,
    }


# ════════════════════════════════════════════════════════════════
#  M2 门禁修复前后
# ════════════════════════════════════════════════════════════════

#: 八种「只有控制面自己的簿记」的证据形态。
_SYS_ONLY_EVIDENCE: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("单条锁簿记", ("sys:lock:wp-x:1",)),
    ("两条锁簿记", ("sys:lock:wp-x:1", "sys:lock:wp-x:2")),
    ("锁 + 失败标记", ("sys:lock:wp-x:1", "sys:worker-failed:wp-x:runtime_error")),
    ("仅失败标记", ("sys:worker-failed:wp-x:timeout",)),
    ("失败 + 真实证据", ("file:runner/result.json", "sys:worker-failed:wp-x:model_error")),
    ("三条簿记", ("sys:lock:wp-x:1", "sys:lock:wp-x:5", "sys:lock:wp-x:9")),
    ("锁 + 未知 sys", ("sys:lock:wp-x:1", "sys:whatever:1")),
    ("仅未知 sys", ("sys:bookkeeping:1",)),
)


def measure_gate_fix() -> dict[str, Any]:
    """同一批注入，跑修复前的门禁与修复后的门禁。

    ★ 这一组不经过 ReconcileLoop，直接对门禁取样 ——
      因为要测的就是门禁本身的判据，不是它被调用的时机。
    """
    fixed = GateRunner()
    register_builtin_gates(fixed)
    legacy = _legacy_runner()

    gate_ids = ("evidence_exists", "self-test", "acceptance", "review")
    leaked_before = leaked_after = 0
    injected = 0
    detail = []
    for name, ev in _SYS_ONLY_EVIDENCE:
        pkt = _packet(
            "wp-gate0001",
            state="review",
            evidence=tuple(EvidenceRef(x) for x in ev),
        )
        # 这一批的共同点：没有任何一条**可作为依据**的证据，或带着失败标记
        for gid in gate_ids:
            injected += 1
            before = legacy.check(gid, pkt).passed
            after = fixed.check(gid, pkt).passed
            leaked_before += before
            leaked_after += after
        detail.append({
            "evidence_shape": name,
            "refs": list(ev),
            "acceptance_evidence": list(acceptance_evidence(ev)),
            "legacy_passed": [legacy.check(g, pkt).passed for g in gate_ids],
            "fixed_passed": [fixed.check(g, pkt).passed for g in gate_ids],
        })
    return {
        "invariant": "I6 证据可判定（门禁层）",
        "fault": "packet 只带控制面自己的 sys: 簿记，或带着 worker 失败标记",
        "violation": "门禁放行",
        "injected": injected,
        "leaked_before_fix": leaked_before,
        "leaked_after_fix": leaked_after,
        "gate_ids": list(gate_ids),
        "cases": detail,
    }


# ════════════════════════════════════════════════════════════════
#  M3 路径锁并发压测
# ════════════════════════════════════════════════════════════════

def measure_lock_race(rounds: int = 400, threads: int = 5) -> dict[str, Any]:
    """真实线程竞争下，重叠路径的实际冲突次数。

    对照组是 `_UnsynchronizedLockTable`：判定逻辑一行不改，只去掉互斥锁。

    ★ `sys.setswitchinterval` 必须压到 1µs，否则这个实验是空转的：
      临界区太短，GIL 在 check-then-act 之间根本没机会切走，
      **把锁整个删掉也是每轮恰好一个赢家**。
      这一条是 08-03 写路径锁时踩出来的，见 docs/项目进展与记忆.md §十五。
    """
    old_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        def one_round(table: LockTable) -> tuple[int, int]:
            """返回 (赢家数, 内部结构损坏次数)。

            ★ 损坏也要数进去。去掉互斥锁的后果不止「多个赢家」——
              前缀树的计数器与节点会真的对不上，`_conflicts_for` 会显式抛
              RuntimeError。那条显式抛出是 08-03 加的（原来是 StopIteration
              被 Python 转成 RuntimeError，堆栈指向调用点，真因被完全掩盖）。
              把它吞掉就等于把这次实验最有说服力的一个观察扔了。
            """
            winners: list[str] = []
            corrupted = 0
            barrier = threading.Barrier(threads)
            lock = threading.Lock()

            def worker(k: int) -> None:
                nonlocal corrupted
                barrier.wait()
                # ★ 先给个明确的 None，而不是依赖 except 分支 return。
                #   mypy 在这里读不出「except 里 return 了所以后面 r 一定有值」，
                #   而它这个保守是对的：以后有人在 except 里改成 continue，
                #   下面那行就会变成 NameError，且只在并发压测里偶发。
                r = None
                try:
                    r = table.acquire(
                        PacketId(f"wp-rc{k:04d}"),
                        ("src/hot/",),
                        at="2026-08-10T00:00:00Z",
                    )
                except RuntimeError:
                    with lock:
                        corrupted += 1
                    return
                if r is not None and r.ok:  # type: ignore[redundant-expr]
                    with lock:
                        winners.append(f"wp-rc{k:04d}")

            ts = [threading.Thread(target=worker, args=(k,)) for k in range(threads)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            return len(winners), corrupted

        locked_bad = 0
        unlocked_bad = 0
        locked_winners = 0
        unlocked_winners = 0
        locked_corrupt = 0
        unlocked_corrupt = 0
        for _ in range(rounds):
            w1, c1 = one_round(LockTable())
            if w1 > 1:
                locked_bad += 1
            locked_winners += w1
            locked_corrupt += bool(c1)
            w2, c2 = one_round(_UnsynchronizedLockTable())
            if w2 > 1:
                unlocked_bad += 1
            unlocked_winners += w2
            unlocked_corrupt += bool(c2)
        return {
            "invariant": "I1 单写者（并发压测）",
            "fault": f"{threads} 线程同时抢同一路径",
            "violation": "同一轮出现多于一个赢家",
            "rounds": rounds,
            "threads": threads,
            "switch_interval_s": 1e-6,
            "leaked_on": locked_bad,
            "leaked_off": unlocked_bad,
            "avg_winners_on": round(locked_winners / rounds, 3),
            "avg_winners_off": round(unlocked_winners / rounds, 3),
            "corrupted_rounds_on": locked_corrupt,
            "corrupted_rounds_off": unlocked_corrupt,
            "corruption_note": (
                "计的是「无互斥锁时 _conflicts_for 显式抛 RuntimeError」的轮数"
                "（前缀树计数器与节点对不上）。这是低频事件，多数次运行为 0，"
                "但在 pytest 的调度下已实际观察到 —— 说明去掉互斥锁的后果"
                "不止「多个赢家」，还可能把结构本身搞坏。"
                "把它单独计数是为了不让这个观察被当成噪音丢掉。"
            ),
            "control_group": (
                "保留冲突检测逻辑、只摘掉互斥锁（不是「没有锁」）——"
                "否则对照组每轮必然 5 个赢家，等于把结论写进对照组"
            ),
        }
    finally:
        sys.setswitchinterval(old_interval)


# ════════════════════════════════════════════════════════════════
#  自绘 SVG（零依赖）
# ════════════════════════════════════════════════════════════════

def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_svg(data: dict[str, Any]) -> str:
    groups = [
        (g["invariant"], g["injected"], g["leaked_off"], g["leaked_on"])
        for g in data["ablation"]
    ]
    gate = data["gate_fix"]
    groups.append((
        gate["invariant"] + "†", gate["injected"],
        gate["leaked_before_fix"], gate["leaked_after_fix"],
    ))
    race = data["lock_race"]
    groups.append((
        race["invariant"], race["rounds"], race["leaked_off"], race["leaked_on"],
    ))

    W, H = 900, 460
    pad_l, pad_r, pad_t, pad_b = 70, 30, 96, 118
    plot_w = W - pad_l - pad_r
    plot_h = H - pad_t - pad_b
    n = len(groups)
    slot = plot_w / n
    bar_w = min(46.0, slot / 3.2)

    def y_of(pct: float) -> float:
        return pad_t + plot_h * (1 - pct)

    out: list[str] = []
    out.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        f'width="{W}" height="{H}" font-family="-apple-system,Segoe UI,Helvetica,Arial,sans-serif">'
    )
    out.append(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')
    out.append(
        f'<text x="{pad_l}" y="34" font-size="17" font-weight="600" fill="#111">'
        f'护栏消融对照：注入的已知故障有多少被静默放行</text>'
    )
    out.append(
        f'<text x="{pad_l}" y="56" font-size="12" fill="#666">'
        f'越高越糟。「护栏关」= ReconcileLoop 的默认配置（四个安全组件默认全是 None）</text>'
    )

    # y 轴网格
    for pct in (0, 0.25, 0.5, 0.75, 1.0):
        y = y_of(pct)
        out.append(
            f'<line x1="{pad_l}" y1="{y:.1f}" x2="{pad_l + plot_w}" y2="{y:.1f}" '
            f'stroke="#e6e6e6" stroke-width="1"/>'
        )
        out.append(
            f'<text x="{pad_l - 10}" y="{y + 4:.1f}" font-size="11" fill="#888" '
            f'text-anchor="end">{int(pct * 100)}%</text>'
        )

    for i, (label, injected, off, on) in enumerate(groups):
        cx = pad_l + slot * (i + 0.5)
        for j, (val, color, name) in enumerate((
            (off, "#c0392b", "护栏关"), (on, "#2d7d46", "护栏开"),
        )):
            pct = (val / injected) if injected else 0.0
            x = cx - bar_w * 1.1 + j * bar_w * 1.15
            y = y_of(pct)
            h = max(pad_t + plot_h - y, 1.5)
            out.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{h:.1f}" '
                f'fill="{color}" rx="2"/>'
            )
            out.append(
                f'<text x="{x + bar_w / 2:.1f}" y="{y - 7:.1f}" font-size="11.5" '
                f'font-weight="600" fill="{color}" text-anchor="middle">'
                f'{val}/{injected}</text>'
            )
        # x 轴标签（按空格/括号折行）
        parts = label.replace("（", "\n（").split("\n")
        for k, part in enumerate(parts):
            out.append(
                f'<text x="{cx:.1f}" y="{pad_t + plot_h + 22 + k * 15:.1f}" '
                f'font-size="11.5" fill="#333" text-anchor="middle">{_esc(part)}</text>'
            )

    out.append(
        f'<line x1="{pad_l}" y1="{pad_t + plot_h:.1f}" x2="{pad_l + plot_w}" '
        f'y2="{pad_t + plot_h:.1f}" stroke="#333" stroke-width="1.4"/>'
    )

    # 图例
    ly = H - 46
    out.append(f'<rect x="{pad_l}" y="{ly - 9}" width="12" height="12" fill="#c0392b" rx="2"/>')
    out.append(
        f'<text x="{pad_l + 18}" y="{ly + 1}" font-size="12" fill="#333">'
        f'护栏关 / 修复前（默认配置）</text>'
    )
    out.append(f'<rect x="{pad_l + 210}" y="{ly - 9}" width="12" height="12" fill="#2d7d46" rx="2"/>')
    out.append(
        f'<text x="{pad_l + 228}" y="{ly + 1}" font-size="12" fill="#333">'
        f'护栏开 / 修复后</text>'
    )
    out.append(
        f'<text x="{pad_l}" y="{H - 18}" font-size="10.5" fill="#777">'
        f'† 门禁那一组是「08-10 修复前 vs 修复后」，不是开关 —— 修复前门禁把控制面自己的 sys: 簿记当成了证据。'
        f'　数据：{_esc(data["generated_at"])}</text>'
    )
    out.append("</svg>")
    return "\n".join(out) + "\n"


# ════════════════════════════════════════════════════════════════
#  main
# ════════════════════════════════════════════════════════════════

def collect(state_dir: Path) -> dict[str, Any]:
    return {
        "experiment": "guardrail-ablation",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "note": (
            "「护栏关」不是人造对照组：ReconcileLoop 的 transition_table / "
            "gate_runner / budget_tracker / guardian 默认值全是 None。"
        ),
        "ablation": [
            measure_lock(state_dir),
            measure_guardian(state_dir),
            measure_budget(state_dir),
        ],
        "gate_fix": measure_gate_fix(),
        "lock_race": measure_lock_race(),
    }


def main() -> int:
    import tempfile

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / ".codentum"
        (state_dir / "packets").mkdir(parents=True)
        data = collect(state_dir)

    (OUT_DIR / "guardrail-ablation.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (OUT_DIR / "guardrail-ablation.svg").write_text(
        render_svg(data), encoding="utf-8"
    )

    print("护栏消融实验")
    print("=" * 62)
    for g in data["ablation"]:
        print(
            f"  {g['invariant']:<22} 注入 {g['injected']:>3}  "
            f"护栏关放行 {g['leaked_off']:>3}  护栏开放行 {g['leaked_on']:>3}"
        )
    gf = data["gate_fix"]
    print(
        f"  {gf['invariant']:<22} 注入 {gf['injected']:>3}  "
        f"修复前放行 {gf['leaked_before_fix']:>3}  修复后放行 {gf['leaked_after_fix']:>3}"
    )
    lr = data["lock_race"]
    print(
        f"  {lr['invariant']:<22} {lr['rounds']} 轮  "
        f"无锁冲突 {lr['leaked_off']:>3}  有锁冲突 {lr['leaked_on']:>3}"
    )
    print("=" * 62)
    print(f"→ {OUT_DIR / 'guardrail-ablation.json'}")
    print(f"→ {OUT_DIR / 'guardrail-ablation.svg'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
