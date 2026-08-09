"""gates —— 门禁模块

每个门禁是一个具名检查，对给定 WorkPacket 判定"能否通过"。

职责：
  注册/管理具名门禁
  执行门禁判定 → GateVerdict（passed + detail + evidence）
  与 TransitionTable 协作：TransitionTable 说"这条转换需要门禁 X"，
  GateRunner 执行 X 并返回结果

不负责：
  实际跑测试（那是 harness 的事，gates 检查测试结果是否存在且通过）
  决定何时调用门禁（那是 reconcile loop 的事）
  管理状态转换（那是 TransitionTable 的事）

确定性，零 LLM。
owner: A ｜ 评审: B
"""

from __future__ import annotations

from .runner import GateRunner, GateVerdict
from .builtin import register_builtin_gates

__all__ = [
    "GateRunner",
    "GateVerdict",
    "register_builtin_gates",
]