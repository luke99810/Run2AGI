"""admission —— 准入校验器

在 WorkPacket 进入系统前执行校验。

职责：
  I2 验收可判定 —— 每个 packet 至少一条机器可判定的验收谓词
  I2 自评审禁止 —— acceptance.authoredBy != packet.role
  路径校验 —— ownsPaths 非空且不与 readsPaths 重叠
  预算校验 —— limitUsd > 0，降级链非空
  依赖校验 —— 无自引用，加入后仍为 DAG
  角色校验 —— role 和 authoredBy 存在于 RoleSpec（与 B 的 loader 对接）
  模型隔离 —— coder != reviewer（B 的 mustDifferFrom 约束）

确定性，零 LLM。
owner: A ｜ 评审: B ｜ 详见 ../../README.md
"""

from __future__ import annotations

from .checker import AdmissionChecker, AdmissionVerdict
from .rules import DEFAULT_RULES, Violation

__all__ = [
    "AdmissionChecker",
    "AdmissionVerdict",
    "DEFAULT_RULES",
    "Violation",
]