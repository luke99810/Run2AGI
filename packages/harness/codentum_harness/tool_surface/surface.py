"""从 RoleSpec 派生工具面."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from codentum_contracts.state import RoleId, RoleSpec

__all__ = [
    "ToolDescriptor",
    "ToolSurface",
    "ToolSurfaceError",
    "derive_tool_surface",
]


class ToolSurfaceError(ValueError):
    """工具面无法从 RoleSpec 派生。"""


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    """执行体可见的一项工具."""

    name: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class ToolSurface:
    """某个角色在一次执行里实际能看见的工具列表."""

    role: RoleId
    tools: tuple[ToolDescriptor, ...]

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(tool.name for tool in self.tools)


def derive_tool_surface(
    spec: RoleSpec,
    registry: Mapping[str, ToolDescriptor],
) -> ToolSurface:
    """按 RoleSpec.tools 白名单派生工具面.

    `registry` 可以包含很多工具, 但返回值只能包含 RoleSpec 明确列出的工具.
    这保证“无权限”先表现为“看不见”, 而不是运行时拦截.
    """
    _reject_duplicate_tools(spec)

    visible: list[ToolDescriptor] = []
    for tool_name in spec.tools:
        tool = registry.get(tool_name)
        if tool is None:
            raise ToolSurfaceError(
                f"RoleSpec[{spec.id}] 声明了未注册工具 {tool_name!r}。"
                "工具缺失必须 fail-closed, 不能静默跳过."
            )
        visible.append(tool)
    return ToolSurface(role=spec.id, tools=tuple(visible))


def _reject_duplicate_tools(spec: RoleSpec) -> None:
    seen: set[str] = set()
    for tool_name in spec.tools:
        if tool_name in seen:
            raise ToolSurfaceError(
                f"RoleSpec[{spec.id}] 重复声明工具 {tool_name!r}。"
                "工具列表必须是稳定白名单, 重复项会让派生结果含义不清."
            )
        seen.add(tool_name)
