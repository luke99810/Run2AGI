"""BudgetTracker —— 全局预算追踪器。

★ 全案预算一律用美元。token 数仅作可观测指标。
★ 告警阈值：80% warn, 95% warn, 100% hard_stop。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from codentum_contracts.state import (
    BudgetAlert,
    BudgetAlertLevel,
    BudgetFile,
)

__all__ = ["BudgetTracker"]


WARN_THRESHOLD_PCT = 0.80
CRITICAL_THRESHOLD_PCT = 0.95
HARD_STOP_THRESHOLD_PCT = 1.00


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class BudgetTracker:
    """全局预算追踪器。

    用法：
        bt = BudgetTracker(limit_usd=20.0)
        if bt.can_afford(5.0):
            bt.spend(2.5, role="coder", model="qwen-plus")
        for alert in bt.alerts:
            print(f"[{alert.level}] {alert.message}")
        bt.save(".codentum/budget.json")
    """

    limit_usd: float
    """全局预算上限。"""

    spent_usd: float = 0.0
    """已支出总额。"""

    by_role: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    """按角色分摊。"""

    by_model: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    """按模型分摊。"""

    degradation_chain: tuple[str, ...] = ()
    """预算不足时的降级顺序。"""

    _alerted_warn: bool = field(default=False, repr=False)
    _alerted_critical: bool = field(default=False, repr=False)
    """防止重复告警。"""

    # ════════════════════════════════════════════════════════
    #  查询
    # ════════════════════════════════════════════════════════

    @property
    def remaining(self) -> float:
        """剩余预算。"""
        return max(0.0, self.limit_usd - self.spent_usd)

    @property
    def usage_pct(self) -> float:
        """已用比例（0.0 ~ 1.0+）。"""
        if self.limit_usd <= 0:
            return 1.0
        return self.spent_usd / self.limit_usd

    def can_afford(self, amount: float) -> bool:
        """预算是否足够支付 amount。"""
        if amount <= 0:
            return True
        return self.spent_usd + amount <= self.limit_usd

    @property
    def alerts(self) -> tuple[BudgetAlert, ...]:
        """当前应触发的告警。已触发过的 warn 不重复。"""
        result: list[BudgetAlert] = []
        pct = self.usage_pct

        if pct >= HARD_STOP_THRESHOLD_PCT:
            result.append(BudgetAlert(
                level="hard_stop",  # type: ignore[arg-type]
                at=_now_iso(),
                message=f"预算已耗尽：${self.spent_usd:.2f} / ${self.limit_usd:.2f}。新 packet 将被准入校验器拒绝。",
            ))
            return tuple(result)

        if pct >= CRITICAL_THRESHOLD_PCT and not self._alerted_critical:
            self._alerted_critical = True
            result.append(BudgetAlert(
                level="warn",  # type: ignore[arg-type]
                at=_now_iso(),
                message=f"预算严重不足：已用 {pct:.0%}（${self.spent_usd:.2f} / ${self.limit_usd:.2f}）。剩余 ${self.remaining:.2f}。建议停止新 packet 准入。",
            ))

        if pct >= WARN_THRESHOLD_PCT and not self._alerted_warn:
            self._alerted_warn = True
            result.append(BudgetAlert(
                level="warn",  # type: ignore[arg-type]
                at=_now_iso(),
                message=f"预算告警：已用 {pct:.0%}（${self.spent_usd:.2f} / ${self.limit_usd:.2f}）。剩余 ${self.remaining:.2f}。",
            ))

        return tuple(result)

    # ════════════════════════════════════════════════════════
    #  变更
    # ════════════════════════════════════════════════════════

    def spend(self, amount: float, *, role: str = "", model: str = "") -> None:
        """记录一笔支出。

        即使超出预算也记录（允许超支但触发 hard_stop 告警）。
        """
        if amount <= 0:
            return

        previous_pct = self.usage_pct
        self.spent_usd += amount

        if role:
            self.by_role[role] = self.by_role.get(role, 0.0) + amount
        if model:
            self.by_model[model] = self.by_model.get(model, 0.0) + amount

        # 如果预算从充足降到不足，重置告警标记
        if previous_pct < HARD_STOP_THRESHOLD_PCT and self.usage_pct >= HARD_STOP_THRESHOLD_PCT:
            self._alerted_warn = True
            self._alerted_critical = True

    def set_limit(self, usd: float) -> None:
        """调整预算上限（如追加预算）。重置告警标记。"""
        if usd <= 0:
            raise ValueError(f"预算上限必须 > 0，收到 {usd}")
        self.limit_usd = usd
        self._alerted_warn = False
        self._alerted_critical = False

    # ════════════════════════════════════════════════════════
    #  持久化
    # ════════════════════════════════════════════════════════

    def to_file(self) -> BudgetFile:
        """导出为 BudgetFile（用于写 .codentum/budget.json）。"""
        return BudgetFile(
            schemaVersion=1,
            currency="USD",
            limitUsd=self.limit_usd,
            spentUsd=self.spent_usd,
            byRole=dict(self.by_role) if self.by_role else None,
            byModel=dict(self.by_model) if self.by_model else None,
            degradationChain=self.degradation_chain or None,
            alerts=self.alerts or None,
        )

    @classmethod
    def from_file(cls, bf: BudgetFile) -> BudgetTracker:
        """从 BudgetFile 重建。"""
        bt = cls(
            limit_usd=bf.limitUsd,
            spent_usd=bf.spentUsd,
            degradation_chain=bf.degradationChain or (),
        )
        if bf.byRole:
            bt.by_role.update(bf.byRole)
        if bf.byModel:
            bt.by_model.update(bf.byModel)
        # 重建告警状态
        if bf.alerts:
            bt._alerted_warn = any(
                a.level == "warn" for a in bf.alerts
            )
        return bt