"""codentum_engine —— 把 delivery 协议接到控制平面上的那个进程。

它不是第四个「层」，是**装配点**：自己不做判定，只决定
「哪些组件被装进 ReconcileLoop」和「哪些能力对外报为可用」。

  packages/delivery   ← C：协议与 sidecar（引擎的对端）
  packages/engine     ← 本包：装配 + 需求入口
  packages/control-plane ← A：ReconcileLoop 与四个安全组件
  packages/harness    ← B：WorkerRuntime 与模型网关

★ 装配点做的两个选择，比它写的代码更重要：
  1. 四个安全组件默认是 None，这里显式全部打开（见 service.py 模块头）
  2. 能力表按「按下去会不会真的发生事情」报，不按路线图报
"""

from __future__ import annotations

from .service import ENGINE_VERSION, EngineConfig, EngineService
from .session import EngineSession

__all__ = [
    "ENGINE_VERSION",
    "EngineConfig",
    "EngineService",
    "EngineSession",
]
