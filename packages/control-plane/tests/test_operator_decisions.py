"""人工审批 / 回滚 —— operator 决定在 reconcile loop 里的落地。

覆盖：
  · manual 验收的 packet 必须 operator approve 才能 review → accepted
  · 无 approve 时 manual packet 永远停在 review（堵 2026-08-12 那个洞）
  · reject → rejected → ready（打回重做）
  · rollback 记录决定并调用注入的 result_rollbacker
  · 决定持久化到 decisions.jsonl，重启后可读回
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from codentum_contracts.state import (
    Acceptance,
    BudgetGrant,
    PacketId,
    Provenance,
    WorkPacket,
    dump_state,
)
from codentum_control_plane.reconcile import ReconcileLoop


@pytest.fixture
def tmp_state_dir() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as td:
        state_dir = Path(td) / ".codentum"
        state_dir.mkdir()
        yield state_dir


def _manual_packet(pid: str, state: str = "review") -> WorkPacket:
    return WorkPacket(
        id=PacketId(pid),
        kind="impl",
        state=state,  # type: ignore[arg-type]
        role="coder",
        ownsPaths=("src/test/",),
        readsPaths=("tests/",),
        deps=(),
        acceptance=Acceptance(
            kind="manual",
            predicate="operator-review: 需人工判定",
            authoredBy="qa",
        ),
        budget=BudgetGrant(
            currency="CNY",
            limitCny=5.0,
            spentCny=0.0,
            degradationChain=("drop_semantic",),
        ),
        attempts=1,
        evidence=(),
        provenance=Provenance(
            createdBy="planner",
            createdAt="2026-08-15T00:00:00Z",
        ),
    )


def _inject(loop: ReconcileLoop, packet: WorkPacket) -> None:
    loop._packets[packet.id] = packet
    pf = Path(loop.state_dir) / "packets" / f"{packet.id}.json"
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(json.dumps(dump_state(packet), indent=2, ensure_ascii=False) + "\n", "utf-8")


def test_manual_packet_stays_review_without_approval(tmp_state_dir: Path) -> None:
    loop = ReconcileLoop(state_dir=str(tmp_state_dir))
    packet = _manual_packet("wp-manual1", state="review")
    _inject(loop, packet)

    report = loop.tick()

    assert report.transitions == ()
    assert loop.packet(PacketId("wp-manual1")).state == "review"


def test_manual_packet_accepted_after_operator_approve(tmp_state_dir: Path) -> None:
    loop = ReconcileLoop(state_dir=str(tmp_state_dir))
    packet = _manual_packet("wp-manual2", state="review")
    _inject(loop, packet)

    # 未审批前不动
    assert loop.tick().transitions == ()

    loop.approve(PacketId("wp-manual2"), note="人工确认可交付")

    report = loop.tick()
    assert [t.to_state for t in report.transitions] == ["accepted"]
    assert loop.packet(PacketId("wp-manual2")).state == "accepted"


def test_manual_packet_reject_then_rejected_to_ready(tmp_state_dir: Path) -> None:
    loop = ReconcileLoop(state_dir=str(tmp_state_dir))
    packet = _manual_packet("wp-manual3", state="review")
    _inject(loop, packet)

    loop.reject(PacketId("wp-manual3"), note="验收不通过，打回")

    report = loop.tick()
    assert [t.to_state for t in report.transitions] == ["rejected"]

    report2 = loop.tick()
    assert [t.to_state for t in report2.transitions] == ["ready"]
    assert loop.packet(PacketId("wp-manual3")).state == "ready"


def test_operator_decisions_are_persisted_and_reloadable(tmp_state_dir: Path) -> None:
    loop = ReconcileLoop(state_dir=str(tmp_state_dir))
    _inject(loop, _manual_packet("wp-manual4", state="review"))

    loop.approve(PacketId("wp-manual4"), note="ok")

    # 重新加载，决定必须还在（重启不丢审批）
    loop2 = ReconcileLoop(state_dir=str(tmp_state_dir))
    loop2.load_state()
    assert loop2._operator_decision(PacketId("wp-manual4"), "approve") is not None
    assert loop2._operator_decision(PacketId("wp-manual4"), "reject") is None


def test_rollback_records_decision_and_invokes_rollbacker(tmp_state_dir: Path) -> None:
    calls: list[str] = []

    def rollbacker(packet: WorkPacket) -> tuple[bool, str]:
        calls.append(str(packet.id))
        return True, "已 revert"

    loop = ReconcileLoop(state_dir=str(tmp_state_dir), result_rollbacker=rollbacker)
    packet = _manual_packet("wp-manual5", state="accepted")
    _inject(loop, packet)

    ok, detail = loop.rollback(PacketId("wp-manual5"), note="产出有问题")

    assert ok is True
    assert calls == ["wp-manual5"]
    # 决定被记录
    assert loop._operator_decision(PacketId("wp-manual5"), "rollback") is not None
    # 状态仍是 accepted（回滚是独立决定，不是状态转换）
    assert loop.packet(PacketId("wp-manual5")).state == "accepted"


def test_rollback_without_rollbacker_returns_false(tmp_state_dir: Path) -> None:
    loop = ReconcileLoop(state_dir=str(tmp_state_dir))
    _inject(loop, _manual_packet("wp-manual6", state="accepted"))

    ok, detail = loop.rollback(PacketId("wp-manual6"))

    assert ok is False
    assert "未配置 result_rollbacker" in detail


def test_approve_unknown_packet_raises(tmp_state_dir: Path) -> None:
    loop = ReconcileLoop(state_dir=str(tmp_state_dir))
    with pytest.raises(ValueError, match="unknown packet"):
        loop.approve(PacketId("wp-missing"))
