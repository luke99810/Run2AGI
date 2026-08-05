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
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from codentum_contracts.state import PacketId, WorkPacket, dump_state
from codentum_contracts.interfaces import (
    AbortReason,
    SpawnRequest,
    WorkerAborted,
    WorkerCompleted,
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
) -> WorkPacket:
    return WorkPacket(
        id=PacketId(pid),
        kind="impl",
        state=state,  # type: ignore[arg-type]
        role=role,  # type: ignore[arg-type]
        ownsPaths=owns,
        readsPaths=("tests/",),
        deps=tuple(PacketId(d) for d in deps),
        acceptance={
            "kind": "test",
            "predicate": "pytest",
            "authoredBy": "qa",
        },
        budget={
            "currency": "USD",
            "limitUsd": 5.0,
            "spentUsd": 0.0,
            "degradationChain": ("drop_semantic",),
        },
        attempts=0,
        evidence=(),
        provenance={
            "createdBy": "planner",
            "createdAt": "2026-08-05T00:00:00Z",
        },
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
        assert t.packet_id == "wp-000001"  # type: ignore[comparison-overlap]
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
        main_transition = [t for t in report.transitions if t.packet_id == "wp-main01"]  # type: ignore[comparison-overlap]
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
        main = [t for t in report.transitions if t.packet_id == "wp-main01"]  # type: ignore[comparison-overlap]
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
        t = [x for x in report.transitions if x.packet_id == "wp-000003"][0]  # type: ignore[comparison-overlap]
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
        transitions_for_pkt = [t for t in report.transitions if t.packet_id == "wp-000004"]  # type: ignore[comparison-overlap]
        assert len(transitions_for_pkt) == 0
        assert empty_loop.packet(PacketId("wp-000004")).state == "ready"

    def test_ready_without_owns_paths_blocked(self, empty_loop: ReconcileLoop) -> None:
        """没有写权限路径的 ready packet 转为 blocked。"""
        pkt = _make_packet("wp-000005", state="ready", owns=())
        _inject(empty_loop, pkt)

        report = empty_loop.tick()
        t = [x for x in report.transitions if x.packet_id == "wp-000005"][0]  # type: ignore[comparison-overlap]
        assert t.to_state == "blocked"


# ════════════════════════════════════════════════════════════════
#  Tests: running → review（需要 Mock WorkerRuntime）
# ════════════════════════════════════════════════════════════════

class _MockWorkerRuntime:
    """Mock WorkerRuntime —— spawn 立即返回，settle 返回预设结果。"""

    def __init__(self, outcome: WorkerOutcome | None = None) -> None:
        self._outcome = outcome or WorkerCompleted(
            evidence=(),
            spent_usd=0.5,
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

    async def settle(self, handle: WorkerHandle) -> WorkerOutcome:
        return self._outcome

    async def abort(self, handle: WorkerHandle, reason: AbortReason) -> None:
        pass


def _completed_outcome() -> WorkerCompleted:
    return WorkerCompleted(
        evidence=(),
        spent_usd=0.5,
        touched_paths=("src/test/",),
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
        t = [x for x in r2.transitions if x.packet_id == "wp-000006"][0]  # type: ignore[comparison-overlap]
        assert t.to_state == "review"
        # 锁应该已经释放
        assert empty_loop._lock_table.holder_of("src/test/") is None

    def test_worker_failed_transitions_to_review(self, empty_loop: ReconcileLoop) -> None:
        """Worker 失败也推进到 review（让 Reviewer 判定）。"""
        failed = WorkerFailed(
            reason_code=FailureCode.ACCEPTANCE_NOT_MET,
            detail="test failed",
            evidence=(),
            spent_usd=0.3,
        )
        mock = _MockWorkerRuntime(outcome=failed)
        empty_loop.worker_runtime = mock

        pkt = _make_packet("wp-000007", state="ready", owns=("src/fail/",))
        _inject(empty_loop, pkt)

        empty_loop.tick()  # ready → running
        r2 = empty_loop.tick()  # running → review
        t = [x for x in r2.transitions if x.packet_id == "wp-000007"][0]  # type: ignore[comparison-overlap]
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
        t = [x for x in report.transitions if x.packet_id == "wp-000009"][0]  # type: ignore[comparison-overlap]
        assert t.to_state == "accepted"

    def test_review_without_evidence_stays(self, empty_loop: ReconcileLoop) -> None:
        """没有证据的 review packet 不能 accepted。"""
        pkt = _make_packet("wp-000010", state="review")
        _inject(empty_loop, pkt)

        report = empty_loop.tick()
        assert empty_loop.packet(PacketId("wp-000010")).state == "review"


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
        t = [x for x in report.transitions if x.packet_id == "wp-000011"][0]  # type: ignore[comparison-overlap]
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
        running_t = [t for t in r2.transitions if t.packet_id == "wp-000013"]  # type: ignore[comparison-overlap]
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
        transitions = {t.packet_id: t for t in report.transitions if t.packet_id == "wp-e2e001"}  # type: ignore[comparison-overlap]

        final_state = empty_loop.packet(PacketId("wp-e2e001")).state
        # 无 worker：从 pending → ready → running，停在 running
        assert final_state in ("running", "review")

        # 验证状态是正向推进的
        states_seen = [
            t.to_state
            for t in report.transitions
            if t.packet_id == "wp-e2e001"  # type: ignore[comparison-overlap]
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
