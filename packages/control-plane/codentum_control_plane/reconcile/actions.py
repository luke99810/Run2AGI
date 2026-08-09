"""Reconcile 动作类型 —— 调和循环每次 tick 产出的记录。

★ 纯数据，无行为。可序列化，可回放。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from codentum_contracts.state import PacketId, PacketState


@dataclass(frozen=True, slots=True)
class PacketTransition:
    """一次状态推进。

    ★ 拒绝也是正常返回值（比如还在等依赖），不视为错误。
    """

    packet_id: PacketId
    from_state: PacketState
    to_state: PacketState
    detail: str

    def __post_init__(self) -> None:
        if self.from_state == self.to_state:
            raise ValueError(
                f"PacketTransition 不允许自环：{self.from_state} → {self.to_state}。"
                f"★ 自环在 reconcile 里没有语义，只会制造无意义的日志噪音。"
            )


@dataclass(frozen=True, slots=True)
class TickReport:
    """一次 reconcile tick 的完整报告。"""

    transitions: tuple[PacketTransition, ...] = ()
    errors: tuple[str, ...] = ()
    ""


@dataclass(frozen=True, slots=True)
class ReconcileContext:
    """单次 tick 的上下文，在 reconcile 各阶段间传递。

    不落盘，仅存活在一次 tick 内。
    """

    dep_states: dict[PacketId, PacketState] = field(default_factory=dict)
    "已解析的依赖状态缓存。包外不可见。"
