"""gates 门禁模块测试

覆盖：
  · GateRunner 注册与执行
  · 内置门禁（evidence_exists / self-test / acceptance / review）
  · 未注册门禁的默认放行
  · 门禁异常处理
  · register_builtin_gates 一键注册
"""

from __future__ import annotations

from typing import cast

import pytest

from codentum_contracts.state import (
    Acceptance,
    BudgetGrant,
    EvidenceRef,
    PacketId,
    Provenance,
    RoleId,
    WorkPacket,
)
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
        acceptance=Acceptance(
            kind= "test",
            predicate= acceptance_predicate,
            authoredBy=cast(RoleId, acceptance_authoredBy),
        ),
        budget=BudgetGrant(
            currency= "CNY",
            limitCny= 5.0,
            spentCny= 0.0,
            degradationChain= ("drop_semantic",),
        ),
        attempts=0,
        evidence=tuple(EvidenceRef(e) for e in evidence),
        provenance=Provenance(
            createdBy= "planner",
            createdAt= "2026-08-05T00:00:00Z",
        ),
    )


class TestGateRunner:
    def test_register_and_check(self) -> None:
        runner = GateRunner()
        def my_gate(pkt: WorkPacket, **_ctx: object) -> GateVerdict:
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
        def broken(pkt: WorkPacket, **_ctx: object) -> GateVerdict:
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

# ════════════════════════════════════════════════════════════════
#  ★ 回归防线：门禁不许拿控制面自己的簿记当证据
#
#  08-09 在 `_try_review_to_accepted` 的**兜底分支**修掉了「系统自己给
#  自己签字」——packet 手上那条 `sys:lock:<pid>` 是 `_try_ready_to_running`
#  自己写的，当时被当成了验收依据。
#
#  但门禁分支没修，而它在 `_try_review_to_accepted` 里**优先于**兜底分支。
#  于是出现了一个反直觉的结果：**配了 gate_runner 的系统比没配的更松** ——
#  打开护栏这个动作，反而绕过了那次修复。
#
#  这一组用例守的就是这件事。把 `builtin.py` 里的 `acceptance_evidence(...)`
#  换回 `packet.evidence`，它们必须变红。
# ════════════════════════════════════════════════════════════════

class TestGatesRejectSysBookkeeping:
    """sys: 前缀的簿记流水不能充当任何门禁的通过依据。"""

    def test_all_gates_reject_sys_only_evidence(self) -> None:
        runner = GateRunner()
        register_builtin_gates(runner)
        # 只有控制面自己写的锁簿记 —— 正是 08-09 那个缺陷的现场
        pkt = _pkt(evidence=(EvidenceRef("sys:lock:wp-gate001:3"),))
        for gate_id in runner.registered:
            v = runner.check(gate_id, pkt)
            assert not v.passed, (
                f"{gate_id} 拿 sys: 簿记当证据放行了 —— "
                f"系统自己给自己签字。detail={v.detail!r}"
            )

    def test_all_gates_reject_worker_failure_marker(self) -> None:
        """worker 明确失败过的 packet，任何门禁都不许放行。

        ★ 判据要点：即使同时带着真实证据也不行。失败标记是**否决项**，
          不是「再找一条好证据就能盖过去」的加权项。
        """
        runner = GateRunner()
        register_builtin_gates(runner)
        pkt = _pkt(evidence=(
            "file:runner/result.json",
            "sys:worker-failed:wp-gate001:runtime_error",
        ))
        for gate_id in runner.registered:
            v = runner.check(gate_id, pkt)
            assert not v.passed, (
                f"{gate_id} 放行了一个 worker 失败过的 packet：{v.detail!r}"
            )

    def test_real_evidence_still_passes(self) -> None:
        """★ 对照组：修复不能靠「把所有东西都拒掉」达成。

        没有这一条，上面两条用「永远返回 False」也能全绿 ——
        那种绿灯与「判得准」无关。
        """
        runner = GateRunner()
        register_builtin_gates(runner)
        pkt = _pkt(evidence=("sys:lock:wp-gate001:3", "file:runner/result.json"))
        for gate_id in runner.registered:
            v = runner.check(gate_id, pkt)
            assert v.passed, f"{gate_id} 把真实证据也拒了：{v.detail!r}"

    def test_passing_verdict_does_not_cite_sys_refs(self) -> None:
        """通过时给出的 evidence_refs 里不能混进 sys: 簿记。

        它会被写进 packet 的验收依据，成为「凭什么放行」的记录。
        混进簿记等于把系统自己的流水写成了外部证明。
        """
        runner = GateRunner()
        register_builtin_gates(runner)
        pkt = _pkt(evidence=("sys:lock:wp-gate001:3", "file:runner/result.json"))
        for gate_id in runner.registered:
            v = runner.check(gate_id, pkt)
            assert v.passed
            assert all(not r.startswith("sys:") for r in v.evidence_refs), (
                f"{gate_id} 把 sys: 簿记写进了验收依据：{v.evidence_refs}"
            )
