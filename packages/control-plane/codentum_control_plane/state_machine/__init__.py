"""WorkPacket 状态机。

转换表从 RoleSpec 派生，本包不持有任何写死的合法转换。
"""

from codentum_control_plane.state_machine.transitions import (
    TERMINAL_STATES,
    TerminalStateError,
    TransitionDenied,
    TransitionTable,
    TransitionVerdict,
    load_role_specs,
)

__all__ = [
    "TERMINAL_STATES",
    "TerminalStateError",
    "TransitionDenied",
    "TransitionTable",
    "TransitionVerdict",
    "load_role_specs",
]
