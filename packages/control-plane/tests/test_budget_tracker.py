"""BudgetTracker 测试

覆盖：
  · 初始化和基本查询
  · spend 和余额计算
  · can_afford 判断
  · 告警生成（80% / 95% / 100%）
  · 持久化往返（to_file / from_file）
  · 从真实 fixture 加载
  · set_limit 重置
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codentum_contracts.state import BudgetFile
from codentum_control_plane.budget import BudgetTracker


class TestInit:
    def test_default_state(self) -> None:
        bt = BudgetTracker(limit_cny=20.0)
        assert bt.limit_cny == 20.0
        assert bt.spent_cny == 0.0
        assert bt.remaining == 20.0
        assert bt.usage_pct == 0.0

    def test_can_afford_initial(self) -> None:
        bt = BudgetTracker(limit_cny=20.0)
        assert bt.can_afford(5.0)
        assert bt.can_afford(20.0)
        assert not bt.can_afford(20.01)

    def test_can_afford_zero(self) -> None:
        bt = BudgetTracker(limit_cny=20.0)
        assert bt.can_afford(0)
        assert bt.can_afford(-1)


class TestSpend:
    def test_spend_updates_state(self) -> None:
        bt = BudgetTracker(limit_cny=20.0)
        bt.spend(5.0, role="coder", model="qwen-plus")
        assert bt.spent_cny == 5.0
        assert bt.remaining == 15.0
        assert bt.usage_pct == 0.25

    def test_spend_tracks_by_role(self) -> None:
        bt = BudgetTracker(limit_cny=20.0)
        bt.spend(3.0, role="coder")
        bt.spend(2.0, role="reviewer")
        bt.spend(1.0, role="coder")
        assert bt.by_role["coder"] == 4.0
        assert bt.by_role["reviewer"] == 2.0

    def test_spend_tracks_by_model(self) -> None:
        bt = BudgetTracker(limit_cny=20.0)
        bt.spend(3.0, model="qwen-plus")
        bt.spend(5.0, model="qwen-max")
        assert bt.by_model["qwen-plus"] == 3.0
        assert bt.by_model["qwen-max"] == 5.0

    def test_spend_zero_ignored(self) -> None:
        bt = BudgetTracker(limit_cny=20.0)
        bt.spend(0.0, role="coder")
        assert bt.spent_cny == 0.0
        assert "coder" not in bt.by_role

    def test_spend_exceeding_limit_allowed(self) -> None:
        """允许超支，但触发 hard_stop 告警。"""
        bt = BudgetTracker(limit_cny=10.0)
        bt.spend(12.0, role="coder")
        assert bt.spent_cny == 12.0
        assert bt.usage_pct > 1.0


class TestAlerts:
    def test_no_alert_below_80(self) -> None:
        bt = BudgetTracker(limit_cny=100.0)
        bt.spend(70.0)
        assert len(bt.alerts) == 0

    def test_warn_at_80(self) -> None:
        bt = BudgetTracker(limit_cny=100.0)
        bt.spend(80.0)
        alerts = bt.alerts
        assert len(alerts) == 1
        assert alerts[0].level == "warn"

    def test_warn_only_once(self) -> None:
        """80% 告警只发一次。"""
        bt = BudgetTracker(limit_cny=100.0)
        bt.spend(80.0)
        assert len(bt.alerts) == 1
        bt.spend(5.0)  # 85%
        assert len(bt.alerts) == 0  # 不重复

    def test_critical_at_95(self) -> None:
        bt = BudgetTracker(limit_cny=100.0)
        bt.spend(80.0)
        _ = bt.alerts  # consume 80% alert
        bt.spend(15.0)  # now at 95%
        alerts = bt.alerts
        assert len(alerts) == 1
        assert alerts[0].level == "warn"
        assert "严重不足" in alerts[0].message

    def test_hard_stop_at_100(self) -> None:
        bt = BudgetTracker(limit_cny=100.0)
        bt.spend(100.0)
        alerts = bt.alerts
        assert len(alerts) == 1
        assert alerts[0].level == "hard_stop"

    def test_hard_stop_repeats(self) -> None:
        """hard_stop 告警每次都发（与 warn 不同）。"""
        bt = BudgetTracker(limit_cny=100.0)
        bt.spend(100.0)
        assert len(bt.alerts) == 1
        assert len(bt.alerts) == 1  # 再次查询仍触发


class TestPersistence:
    def test_to_file(self) -> None:
        bt = BudgetTracker(limit_cny=20.0, degradation_chain=("drop_semantic",))
        bt.spend(5.0, role="coder", model="qwen-plus")
        bf = bt.to_file()
        assert bf.schemaVersion == 1
        assert bf.currency == "CNY"
        assert bf.limitCny == 20.0
        assert bf.spentCny == 5.0
        assert bf.byRole == {"coder": 5.0}
        assert bf.byModel == {"qwen-plus": 5.0}

    def test_from_file(self) -> None:
        bf = BudgetFile(
            schemaVersion=1, currency="CNY",
            limitCny=20.0, spentCny=8.5,
            byRole={"coder": 5.0, "reviewer": 3.5},
            byModel={"qwen-plus": 8.5},
            degradationChain=("drop_semantic",),
        )
        bt = BudgetTracker.from_file(bf)
        assert bt.limit_cny == 20.0
        assert bt.spent_cny == 8.5
        assert bt.by_role["coder"] == 5.0
        assert bt.by_model["qwen-plus"] == 8.5

    def test_roundtrip(self) -> None:
        bt = BudgetTracker(limit_cny=30.0)
        bt.spend(12.0, role="coder", model="qwen-max")
        bt.spend(3.0, role="qa", model="qwen-plus")
        bt2 = BudgetTracker.from_file(bt.to_file())
        assert bt2.limit_cny == bt.limit_cny
        assert bt2.spent_cny == bt.spent_cny
        assert bt2.by_role == dict(bt.by_role)

    def test_from_real_fixture(self) -> None:
        """从 mid-flight fixture 加载。"""
        fpath = Path(__file__).resolve().parents[3] / "fixtures" / "golden-state" / "mid-flight" / ".codentum" / "budget.json"
        if not fpath.exists():
            pytest.skip("fixture not found")
        with open(fpath, "r") as f:
            raw = json.load(f)
        bf = BudgetFile.model_validate(raw)
        bt = BudgetTracker.from_file(bf)
        assert bt.limit_cny == 20.0
        assert bt.spent_cny == pytest.approx(4.82)
        assert bt.remaining == pytest.approx(15.18)


class TestSetLimit:
    def test_set_limit_increases(self) -> None:
        bt = BudgetTracker(limit_cny=10.0)
        bt.spend(9.0)
        assert bt.usage_pct == 0.9
        bt.set_limit(20.0)
        assert bt.limit_cny == 20.0
        assert bt.usage_pct == 0.45

    def test_set_limit_resets_alerts(self) -> None:
        bt = BudgetTracker(limit_cny=10.0)
        bt.spend(9.0)
        _ = bt.alerts  # trigger warn
        bt.set_limit(100.0)
        assert len(bt.alerts) == 0  # 告警重置