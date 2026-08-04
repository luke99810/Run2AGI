from __future__ import annotations

import pytest
from codentum_contracts.state import RoleSpec
from codentum_harness.tool_surface import ToolDescriptor, ToolSurfaceError, derive_tool_surface


def spec_with_tools(*tools: str) -> RoleSpec:
    return RoleSpec(
        id="coder",
        usesModel=True,
        writes=(),
        reads=(),
        tools=tools,
        transitions=(),
    )


def test_tool_surface_is_derived_from_rolespec_allowlist() -> None:
    registry = {
        "read_file": ToolDescriptor("read_file"),
        "write_file": ToolDescriptor("write_file"),
        "write_contract": ToolDescriptor("write_contract"),
    }

    surface = derive_tool_surface(spec_with_tools("read_file", "write_file"), registry)

    assert surface.tool_names == ("read_file", "write_file")
    assert "write_contract" not in surface.tool_names


def test_registry_order_cannot_leak_tools_or_reorder_surface() -> None:
    registry = {
        "write_contract": ToolDescriptor("write_contract"),
        "write_file": ToolDescriptor("write_file"),
        "read_file": ToolDescriptor("read_file"),
    }

    surface = derive_tool_surface(spec_with_tools("read_file", "write_file"), registry)

    assert surface.tool_names == ("read_file", "write_file")


def test_missing_declared_tool_fails_closed() -> None:
    with pytest.raises(ToolSurfaceError, match="未注册工具"):
        derive_tool_surface(
            spec_with_tools("read_file", "missing_tool"),
            {"read_file": ToolDescriptor("read_file")},
        )


def test_duplicate_tool_declaration_is_rejected() -> None:
    registry = {"read_file": ToolDescriptor("read_file")}

    with pytest.raises(ToolSurfaceError, match="重复声明工具"):
        derive_tool_surface(spec_with_tools("read_file", "read_file"), registry)
