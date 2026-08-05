"""reconcile —— 调和循环

★ 对标 K8s controller manager。控制平面的心脏。

职责：轮询所有 WorkPacket，根据当前状态和依赖关系决定下一步动作，
      推进状态、申请锁、派发 Worker、过门禁。

★ 必须幂等。同一状态跑两次 reconcile 不会产生副作用。
★ 必须确定性。零 LLM，零随机。
★ 不 import codentum_harness。WorkerRuntime 通过注入获得。

owner: A ｜ 评审: B ｜ 详见 ../../README.md
"""

from __future__ import annotations

from .actions import PacketTransition, TickReport
from .loop import ReconcileLoop

__all__ = [
    "PacketTransition",
    "ReconcileLoop",
    "TickReport",
]
