"""工具面派生.

工具面必须从 RoleSpec 派生. 未授权工具不进入列表; 调用时再拒绝已经太晚,
因为模型已经看见了那条路.
"""

from .surface import ToolDescriptor, ToolSurface, ToolSurfaceError, derive_tool_surface

__all__ = [
    "ToolDescriptor",
    "ToolSurface",
    "ToolSurfaceError",
    "derive_tool_surface",
]
