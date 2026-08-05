"""budget —— 预算追踪

全案预算一律用美元（USD），不用 token。
不同模型家族的分词器差异可达 ~30%，异构路由下按 token 记预算会静默失真。

职责：
  追踪全局支出（limitUsd / spentUsd）
  按角色和模型分摊成本
  告警生成（80% / 95% / 100%）
  持久化到 .codentum/budget.json

确定性，零 LLM。
owner: A ｜ 评审: B
"""

from __future__ import annotations

from .tracker import BudgetTracker

__all__ = ["BudgetTracker"]