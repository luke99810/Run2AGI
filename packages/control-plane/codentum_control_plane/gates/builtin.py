"""内置门禁实现。

P0 阶段提供三个基础门禁：
  evidence_exists  — packet 有非空证据（最基础的检查）
  self_test        — 检查是否存在测试通过的证据
  acceptance       — 检查验收测试是否通过
  review           — 检查评审是否完成

★ P0 简化：这些门禁只检查证据是否存在且格式正确。
  完整版需要实际读取证据文件内容并做交叉校验。
"""

from __future__ import annotations

from codentum_contracts.state import EvidenceRef, WorkPacket

from .runner import GateRunner, GateVerdict

__all__ = [
    "evidence_exists_gate",
    "self_test_gate",
    "acceptance_gate",
    "review_gate",
    "register_builtin_gates",
]


def evidence_exists_gate(
    packet: WorkPacket,
    **_ctx: object,
) -> GateVerdict:
    """最基础的门禁：packet 必须携带至少一条证据。

    ★ 这是 I6 在门禁层的强制点。
    状态推进必须附证据引用，声明不算。
    """
    if not packet.evidence:
        return GateVerdict(
            passed=False,
            gate_id="evidence_exists",
            detail="packet 没有携带任何证据。I6：状态推进必须附证据引用。",
        )
    return GateVerdict(
        passed=True,
        gate_id="evidence_exists",
        detail=f"packet 携带 {len(packet.evidence)} 条证据",
        evidence_refs=packet.evidence,
    )


def self_test_gate(
    packet: WorkPacket,
    **_ctx: object,
) -> GateVerdict:
    """自测门禁：Coder 提交前必须跑通自己的测试。

    P0 简化：检查是否有证据且 packet 的 acceptance 非空。
    完整版：解析测试报告证据，确认全部通过。
    """
    if not packet.evidence:
        return GateVerdict(
            passed=False,
            gate_id="self-test",
            detail="自测门禁未通过：缺少测试证据。Coder 必须提交测试通过的证据。",
        )

    if not packet.acceptance.predicate.strip():
        return GateVerdict(
            passed=False,
            gate_id="self-test",
            detail="自测门禁未通过：验收谓词为空。",
        )

    return GateVerdict(
        passed=True,
        gate_id="self-test",
        detail=f"自测门禁通过（验收谓词: {packet.acceptance.predicate[:80]}）",
        evidence_refs=packet.evidence,
    )


def acceptance_gate(
    packet: WorkPacket,
    **_ctx: object,
) -> GateVerdict:
    """验收门禁：QA 写的验收测试必须全部通过。

    P0 简化：检查 evidence 非空且 acceptance.authoredBy != packet.role。
    完整版：实际运行验收测试并检查结果。
    """
    if not packet.evidence:
        return GateVerdict(
            passed=False,
            gate_id="acceptance",
            detail="验收门禁未通过：缺少验收证据。",
        )

    # ★ I2：验收的作者不能是执行者自己
    if packet.acceptance.authoredBy == packet.role:
        return GateVerdict(
            passed=False,
            gate_id="acceptance",
            detail=(
                f"验收门禁未通过：验收由 {packet.role!r} 自己编写（I2 违规）。"
                f"验收必须由不同于执行者的角色编写。"
            ),
        )

    return GateVerdict(
        passed=True,
        gate_id="acceptance",
        detail=f"验收门禁通过（验收作者: {packet.acceptance.authoredBy!r}）",
        evidence_refs=packet.evidence,
    )


def review_gate(
    packet: WorkPacket,
    **_ctx: object,
) -> GateVerdict:
    """评审门禁：Reviewer 必须完成评审并批准。

    P0 简化：检查 evidence 非空（视为评审已完成）。
    完整版：读取评审证据，确认评审结果为"批准"而非"需要修改"。
    """
    if not packet.evidence:
        return GateVerdict(
            passed=False,
            gate_id="review",
            detail="评审门禁未通过：缺少评审证据。Reviewer 必须提交评审结论。",
        )

    return GateVerdict(
        passed=True,
        gate_id="review",
        detail=f"评审门禁通过（{len(packet.evidence)} 条证据）",
        evidence_refs=packet.evidence,
    )


def register_builtin_gates(runner: GateRunner) -> None:
    """向 GateRunner 注册全部内置门禁。

    调用方只需：
        runner = GateRunner()
        register_builtin_gates(runner)
    """
    runner.register("evidence_exists", evidence_exists_gate)
    runner.register("self-test", self_test_gate)
    runner.register("acceptance", acceptance_gate)
    runner.register("review", review_gate)