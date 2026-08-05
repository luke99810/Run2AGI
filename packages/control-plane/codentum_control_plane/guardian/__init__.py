"""guardian —— 确定性拦截器

控制平面唯一的「角色」—— 唯一 usesModel = false 的。
I3 契约冻结 + I5 单会话闭合 + 拦截记录。
零 LLM。
"""

from __future__ import annotations

from .checker import Guardian, Interception, InterceptionCode

__all__ = ["Guardian", "Interception", "InterceptionCode"]