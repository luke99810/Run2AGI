"""gates 门禁模块测试

覆盖：
  · GateRunner 注册与执行
  · 内置门禁（evidence_exists / self-test / acceptance / review）
  · 未注册门禁的默认放行
  · 门禁异常处理
  · register_builtin_gates 一键注册
"""

from __future__ import annotations

import pytest

from codentum_contracts.state import PacketId, WorkPacket
from codentum_control_plane.gates import GateRunner, GateVerdict, register_builtin_gates
from codentum_control_plane.gates.builtin import (
    evidence_exists_gate,
    self_test_gate,
    acceptance_gate,
    review_gate,
)


def _pkt(
    pid: str = "wp-gate001",
    state: str = "review",
    role: str = "coder",
    evidence: tuple[str, ...] = ("ev-001",),
    acceptance_predicate: str = "pytest",
    acceptance_authoredBy: str = "qa",
) -> WorkPacket:
    return WorkPacket(
        id=PacketId(pid),
        kind="impl",
        state=state,  # type: ignore[arg-type]
        role=role,  # type: ignore[arg-type]
        ownsPaths=("src/test/",),
        readsPaths=(),
        deps=(),
        acceptance={
            "kind": "test",
            "predicate": acceptance_predicate,
            "authoredBy": acceptance_authoredBy,
        },
        budget={
            "currency": "USD",
            "limitUsd": 5.0,
            "spentUsd": 0.0,
            "degradationChain": ("drop_semantic",),
        },
        attempts=0,
        evidence=tuple(evidence),
        provenance={
            "createdBy": "planner",
            "createdAt": "2026-08-05T00:00:00Z",
        },
    )


class TestGateRunner:
    def test_register_and_check(self) -> None:
        runner = GateRunner()
        def my_gate(pkt, **_ctx):
            return GateVerdict(passed=True, gate_id="my", detail="ok")
        runner.register("my", my_gate)
        v = runner.check("my", _pkt())
        assert v.passed
        assert v.gate_id == "my"

    def test_unregistered_gate_passes(self) -> None:
        """未注册的门禁默认放行（P0 设计决策）。"""
        runner = GateRunner()
        v = runner.check("nonexistent", _pkt())
        assert v.passed
        assert "未注册" in v.detail

    def test_has(self) -> None:
        runner = GateRunner()
        assert not runner.has("test-gate")
        runner.register("test-gate", evidence_exists_gate)
        assert runner.has("test-gate")

    def test_registered_returns_sorted(self) -> None:
        runner = GateRunner()
        runner.register("z", evidence_exists_gate)
        runner.register("a", evidence_exists_gate)
        assert runner.registered == ("a", "z")

    def test_gate_exception_returns_failed(self) -> None:
        """门禁抛异常 → 返回 passed=False 而非崩溃。"""
        runner = GateRunner()
        def broken(pkt, **_ctx):
            raise RuntimeError("boom")
        runner.register("broken", broken)
        v = runner.check("broken", _pkt())
        assert not v.passed
        assert "boom" in v.detail

    def test_register_overwrites(self) -> None:
        """同名注册覆盖。"""
        runner = GateRunner()
        runner.register("g", lambda p, **c: GateVerdict(False, "g", "v1"))
        runner.register("g", lambda p, **c: GateVerdict(True, "g", "v2"))
        v = runner.check("g", _pkt())
        assert v.passed


class TestEvidenceExistsGate:
    def test_with_evidence_passes(self) -> None:
        v = evidence_exists_gate(_pkt(evidence=("ev-001", "ev-002")))
        assert v.passed

    def test_without_evidence_fails(self) -> None:
        v = evidence_exists_gate(_pkt(evidence=()))
        assert not v.passed


class TestSelfTestGate:
    def test_passes(self) -> None:
        v = self_test_gate(_pkt())
        assert v.passed

    def test_no_evidence_fails(self) -> None:
        v = self_test_gate(_pkt(evidence=()))
        assert not v.passed

    def test_empty_predicate_fails(self) -> None:
        v = self_test_gate(_pkt(acceptance_predicate="   "))
        assert not v.passed


class TestAcceptanceGate:
    def test_passes(self) -> None:
        v = acceptance_gate(_pkt(role="coder", acceptance_authoredBy="qa"))
        assert v.passed

    def test_self_authored_fails(self) -> None:
        """验收由自己编写 → 失败（I2）"""
        v = acceptance_gate(_pkt(role="coder", acceptance_authoredBy="coder"))
        assert not v.passed

    def test_no_evidence_fails(self) -> None:
        v = acceptance_gate(_pkt(evidence=()))
        assert not v.passed


class TestReviewGate:
    def test_passes(self) -> None:
        v = review_gate(_pkt())
        assert v.passed

    def test_no_evidence_fails(self) -> None:
        v = review_gate(_pkt(evidence=()))
        assert not v.passed


class TestRegisterBuiltinGates:
    def test_registers_all_four(self) -> None:
        runner = GateRunner()
        register_builtin_gates(runner)
        assert runner.has("evidence_exists")
        assert runner.has("self-test")
        assert runner.has("acceptance")
        assert runner.has("review")
        assert len(runner.registered) == 4

    def test_all_gates_work(self) -> None:
        """一键注册后，全部门禁都能正常执行。"""
        runner = GateRunner()
        register_builtin_gates(runner)
        pkt = _pkt()
        for gate_id in runner.registered:
            v = runner.check(gate_id, pkt)
            assert v.passed, f"{gate_id} failed: {v.detail}"

    def test_all_gates_fail_without_evidence(self) -> None:
        """无 evidence 时，全部门禁都应该失败。"""
        runner = GateRunner()
        register_builtin_gates(runner)
        pkt = _pkt(evidence=())
        for gate_id in runner.registered:
            v = runner.check(gate_id, pkt)
            assert not v.passed, f"{gate_id} should fail without evidence"