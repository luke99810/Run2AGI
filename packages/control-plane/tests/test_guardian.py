"""Guardian 确定性拦截器测试

覆盖：
  · I3 契约冻结：拦截非 architect 对 contracts/ 的写入
  · I3 契约冻结：architect 可以写 frozen_paths
  · I5 单会话闭合：超过 max_attempts 被拦截
  · I5 预算耗尽检查
  · 拦截记录与清空
  · RoleSpec 写权限检查
"""

from __future__ import annotations

import pytest

from codentum_contracts.state import (
    Acceptance,
    BudgetGrant,
    ModelPolicy,
    PacketId,
    Provenance,
    RoleSpec,
    RoleTransition,
    WorkPacket,
)
from codentum_control_plane.guardian import Guardian, Interception


def _pkt(pid: str = "wp-guard01", role: str = "coder", attempts: int = 0) -> WorkPacket:
    return WorkPacket(
        id=PacketId(pid), kind="impl", state="running",
        role=role,  # type: ignore[arg-type]
        ownsPaths=("src/test/",), readsPaths=(), deps=(),
        acceptance=Acceptance(kind="test", predicate="pytest", authoredBy="qa"),
        budget=BudgetGrant(
            currency="CNY", limitCny=5.0, spentCny=0.0, degradationChain=("x",)
        ),
        attempts=attempts, evidence=(),
        provenance=Provenance(createdBy="planner", createdAt="2026-08-05T00:00:00Z"),
    )


def _coder_spec() -> RoleSpec:
    return RoleSpec(
        id="coder", summary="", usesModel=True,
        writes=("workspace/**",), reads=("packages/contracts/**",),
        tools=("t",),
        transitions=(RoleTransition.model_validate(({"from": "running", "to": "review"})),),
        modelPolicy=ModelPolicy(defaultModel="qwen-plus", defaultEffort="medium"),
    )


def _architect_spec() -> RoleSpec:
    return RoleSpec(
        id="architect", summary="", usesModel=True,
        writes=("packages/contracts/**",), reads=(),
        tools=("t",),
        transitions=(RoleTransition.model_validate(({"from": "running", "to": "review"})),),
        modelPolicy=ModelPolicy(defaultModel="qwen-max", defaultEffort="high"),
    )


class TestI3ContractFreeze:
    def test_coder_blocked_from_contracts(self) -> None:
        g = Guardian()
        ok, reason = g.check_path_write(_pkt(role="coder"), "packages/contracts/schema.json")
        assert not ok
        assert "I3" in reason
        assert g.interception_count == 1

    def test_architect_allowed_to_contracts(self) -> None:
        g = Guardian()
        ok, _ = g.check_path_write(_pkt(role="architect"), "packages/contracts/schema.json")
        assert ok
        assert g.interception_count == 0

    def test_coder_allowed_to_workspace(self) -> None:
        g = Guardian()
        ok, _ = g.check_path_write(_pkt(role="coder"), "src/app/main.py")
        assert ok

    def test_subdirectory_of_frozen_blocked(self) -> None:
        """contracts/ 的子目录也被拦截。"""
        g = Guardian()
        ok, _ = g.check_path_write(_pkt(role="coder"), "packages/contracts/schemas/budget.schema.json")
        assert not ok

    def test_custom_frozen_paths(self) -> None:
        g = Guardian(frozen_paths=("secrets/", "config/production/"))
        ok, _ = g.check_path_write(_pkt(role="coder"), "secrets/api-key.txt")
        assert not ok
        ok, _ = g.check_path_write(_pkt(role="coder"), "src/main.py")
        assert ok

    def test_rolespec_write_check(self) -> None:
        """提供 RoleSpec 时额外检查写权限。"""
        g = Guardian()
        specs = (_coder_spec(),)
        # coder 只能写 workspace/**，不能写 docs/
        ok, reason = g.check_path_write(
            _pkt(role="coder"), "docs/readme.md", role_specs=specs)
        assert not ok
        assert "不允许写入" in reason

    def test_rolespec_allows_valid_path(self) -> None:
        g = Guardian()
        specs = (_coder_spec(),)
        ok, _ = g.check_path_write(
            _pkt(role="coder"), "workspace/src/main.py", role_specs=specs)
        assert ok


class TestI5SessionClosure:
    def test_under_limit_allowed(self) -> None:
        g = Guardian(max_attempts=3)
        ok, _ = g.check_attempts(_pkt(attempts=2))
        assert ok

    def test_at_limit_blocked(self) -> None:
        g = Guardian(max_attempts=3)
        ok, reason = g.check_attempts(_pkt(attempts=3))
        assert not ok
        assert "I5" in reason

    def test_over_limit_blocked(self) -> None:
        g = Guardian(max_attempts=3)
        ok, _ = g.check_attempts(_pkt(attempts=5))
        assert not ok

    def test_budget_exhausted(self) -> None:
        g = Guardian()
        ok, reason = g.check_budget(_pkt(), remaining=0.0)
        assert not ok
        assert "I5" in reason

    def test_budget_sufficient(self) -> None:
        g = Guardian()
        ok, _ = g.check_budget(_pkt(), remaining=5.0)
        assert ok


class TestInterceptionRecords:
    def test_interceptions_accumulate(self) -> None:
        g = Guardian()
        g.check_path_write(_pkt(role="coder"), "packages/contracts/x.json")
        g.check_path_write(_pkt(role="coder"), "packages/contracts/y.json")
        assert g.interception_count == 2

    def test_drain_clears(self) -> None:
        g = Guardian()
        g.check_path_write(_pkt(role="coder"), "packages/contracts/x.json")
        records = g.drain_interceptions()
        assert len(records) == 1
        assert g.interception_count == 0

    def test_drain_increments_total(self) -> None:
        g = Guardian()
        g.check_path_write(_pkt(role="coder"), "packages/contracts/x.json")
        g.drain_interceptions()
        assert g._total_intercepted == 1
