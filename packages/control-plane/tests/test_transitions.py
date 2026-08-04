"""状态机单元测试。

★ 本文件里的 RoleSpec 全部是**测试就地构造**的，不引用 packages/roles 的真实配置 ——
  状态机的正确性不该依赖某一版角色配置长什么样。反过来说：真实 RoleSpec 写错了，
  该由 roles 那边的测试发现，不该由这里。
"""

from __future__ import annotations

import pytest
from codentum_contracts.state import EvidenceRef, RoleId, RoleSpec, RoleTransition
from codentum_control_plane.state_machine import (
    TERMINAL_STATES,
    TerminalStateError,
    TransitionDenied,
    TransitionTable,
    load_role_specs,
)

EV = (EvidenceRef("ev-001"),)


def spec(
    role: RoleId,
    transitions: list[tuple[str, str, str | None]],
    *,
    uses_model: bool = True,
) -> RoleSpec:
    return RoleSpec(
        id=role,
        usesModel=uses_model,
        writes=(),
        reads=(),
        tools=(),
        transitions=tuple(
            RoleTransition.model_validate({"from": f, "to": t, "requiresGate": g})
            for f, t, g in transitions
        ),
    )


# ── 派生，而不是硬编码 ───────────────────────────────────────


def test_table_is_derived_from_rolespec_not_hardcoded() -> None:
    """★ 本模块最核心的性质：换一份 RoleSpec，表就换。

    如果哪天有人图省事在模块里写死一张转换表，这条会红 ——
    因为写死的表不会因为传入不同的 RoleSpec 而改变。
    """
    a = TransitionTable([spec("coder", [("ready", "running", None)])])
    b = TransitionTable([spec("coder", [("running", "review", "acceptance")])])

    assert a.check(role="coder", current="ready", target="running", evidence=EV).allowed
    assert not b.check(role="coder", current="ready", target="running", evidence=EV).allowed
    assert b.check(role="coder", current="running", target="review", evidence=EV).allowed


def test_gate_requirement_is_surfaced_not_executed() -> None:
    """本模块只回答「要不要过门禁」，不跑门禁。"""
    table = TransitionTable([spec("reviewer", [("review", "accepted", "green-line")])])
    v = table.check(role="reviewer", current="review", target="accepted", evidence=EV)
    assert v.allowed
    assert v.requires_gate == "green-line"


def test_no_gate_means_none_not_empty_string() -> None:
    table = TransitionTable([spec("planner", [("pending", "ready", None)])])
    v = table.check(role="planner", current="pending", target="ready", evidence=EV)
    assert v.allowed
    assert v.requires_gate is None


# ── I6 证据 ──────────────────────────────────────────────────


def test_transition_without_evidence_is_denied() -> None:
    """★ I6：状态推进必须附证据引用，声明不算。"""
    table = TransitionTable([spec("coder", [("running", "review", None)])])
    v = table.check(role="coder", current="running", target="review", evidence=())
    assert not v.allowed
    assert v.reason == "missing_evidence"


def test_evidence_requirement_applies_to_every_transition() -> None:
    """不是只有关键转换要证据 —— 每一条都要，否则「哪些算关键」会变成可争论的。"""
    table = TransitionTable(
        [spec("manager", [("pending", "ready", None), ("ready", "blocked", None)])]
    )
    for current, target in [("pending", "ready"), ("ready", "blocked")]:
        assert not table.check(role="manager", current=current, target=target, evidence=()).allowed


# ── 角色权限 ─────────────────────────────────────────────────


def test_role_cannot_use_another_roles_transition() -> None:
    table = TransitionTable(
        [
            spec("coder", [("ready", "running", None)]),
            spec("reviewer", [("review", "accepted", "acceptance")]),
        ]
    )
    v = table.check(role="coder", current="review", target="accepted", evidence=EV)
    assert not v.allowed
    assert v.reason == "unknown_transition"
    # 拒绝理由要指出该转换属于谁 —— 不可操作的拒绝只会让调用方盲目重试
    assert "reviewer" in v.detail


def test_unknown_role_is_denied_with_distinct_reason() -> None:
    table = TransitionTable([spec("coder", [("ready", "running", None)])])
    v = table.check(role="qa", current="ready", target="running", evidence=EV)
    assert not v.allowed
    assert v.reason == "role_not_permitted"


# ── 终态 ─────────────────────────────────────────────────────


def test_terminal_states_cannot_transition_out() -> None:
    table = TransitionTable([spec("manager", [("ready", "running", None)])])
    for terminal in TERMINAL_STATES:
        v = table.check(role="manager", current=terminal, target="ready", evidence=EV)
        assert not v.allowed
        assert v.reason == "terminal_state"


def test_rejected_is_not_terminal() -> None:
    """★ 最容易被顺手写进终态集的一个。

    写进去不报错，只是所有被打回的 packet 都悄悄卡死 —— 而"卡死"和
    "还没轮到它"在看板上长得一样。
    """
    assert "rejected" not in TERMINAL_STATES
    table = TransitionTable([spec("manager", [("rejected", "ready", None)])])
    assert table.check(role="manager", current="rejected", target="ready", evidence=EV).allowed


def test_rolespec_declaring_transition_out_of_terminal_is_rejected_at_load() -> None:
    """在加载期拒绝，而不是等运行时才发现。"""
    with pytest.raises(TerminalStateError, match="终态"):
        TransitionTable([spec("manager", [("accepted", "ready", None)])])


def test_same_state_is_not_a_transition() -> None:
    table = TransitionTable([spec("coder", [("ready", "running", None)])])
    v = table.check(role="coder", current="ready", target="ready", evidence=EV)
    assert not v.allowed
    assert v.reason == "same_state"


def test_self_loop_in_rolespec_is_rejected_at_load() -> None:
    """自环会给 I6 开一个洞：原地反复转换即可不断产生看似合法的状态变更记录。"""
    with pytest.raises(ValueError, match="自环"):
        TransitionTable([spec("coder", [("running", "running", None)])])


# ── 加载期的跨字段约束（schema 表达不了的）─────────────────


def test_guardian_must_not_use_model() -> None:
    """★ rolespec.schema.json 明确说了这条「由加载 RoleSpec 的代码强制」。

    这就是那段代码。schema 过了不代表它被检查过 —— schema 自己写着这句警告。
    """
    with pytest.raises(ValueError, match="guardian"):
        load_role_specs([spec("guardian", [("review", "rejected", None)], uses_model=True)])


def test_guardian_without_model_loads_fine() -> None:
    load_role_specs([spec("guardian", [("review", "rejected", None)], uses_model=False)])


def test_duplicate_rolespec_is_rejected() -> None:
    with pytest.raises(ValueError, match="重复"):
        TransitionTable(
            [
                spec("coder", [("ready", "running", None)]),
                spec("coder", [("running", "review", None)]),
            ]
        )


def test_conflicting_gate_for_same_transition_is_rejected() -> None:
    """同一角色对同一转换声明两个不同门禁 —— 取哪个都是猜，所以不取。"""
    with pytest.raises(ValueError, match="两个不同的门禁"):
        TransitionTable(
            [
                spec(
                    "reviewer",
                    [("review", "accepted", "green-line"), ("review", "accepted", "acceptance")],
                )
            ]
        )


# ── 覆盖度查询 ───────────────────────────────────────────────


def test_unreachable_states_are_reported() -> None:
    """★ 一个进不去的状态不会报错，它只是永远为空 ——
    而"永远为空"和"这条路径没被走到过"在看板上长得一模一样。
    """
    table = TransitionTable([spec("coder", [("ready", "running", None)])])
    unreachable = table.unreachable_states(
        ["pending", "ready", "running", "blocked", "review", "accepted", "rejected", "abandoned"]
    )
    assert "running" not in unreachable  # 能进
    assert "ready" in unreachable  # 没有任何转换以它为目标
    assert "accepted" in unreachable


def test_roles_allowing_and_targets_from() -> None:
    table = TransitionTable(
        [
            spec("coder", [("ready", "running", None)]),
            spec("manager", [("ready", "running", None), ("ready", "abandoned", None)]),
        ]
    )
    assert table.roles_allowing("ready", "running") == frozenset({"coder", "manager"})
    assert table.targets_from("manager", "ready") == frozenset({"running", "abandoned"})
    assert table.targets_from("coder", "ready") == frozenset({"running"})


# ── 异常风格 ─────────────────────────────────────────────────


def test_assert_allowed_raises_with_verdict_attached() -> None:
    table = TransitionTable([spec("coder", [("ready", "running", "secret-scan")])])
    assert table.assert_allowed(role="coder", current="ready", target="running", evidence=EV) == "secret-scan"

    with pytest.raises(TransitionDenied) as excinfo:
        table.assert_allowed(role="coder", current="ready", target="running", evidence=())
    assert excinfo.value.verdict.reason == "missing_evidence"
