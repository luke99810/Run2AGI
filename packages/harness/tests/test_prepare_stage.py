from __future__ import annotations

from pathlib import Path

import pytest
from codentum_contracts import BudgetGrantRuntime, ModelRouting, PacketId, RoleSpec
from codentum_harness.prepare import prepare_spawn_request
from codentum_harness.tool_surface import ToolDescriptor


def role_spec() -> RoleSpec:
    return RoleSpec(
        id="coder",
        usesModel=True,
        writes=("workspace/src/**",),
        reads=("packages/contracts/**",),
        tools=("read_file", "write_file"),
        transitions=(),
    )


def test_prepare_spawn_request_derives_tools_mounts_and_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workers" / "wp-abcdef"
    prepared = prepare_spawn_request(
        packet_id=PacketId("wp-abcdef"),
        role_spec=role_spec(),
        tool_registry={
            "read_file": ToolDescriptor("read_file"),
            "write_file": ToolDescriptor("write_file"),
            "write_contract": ToolDescriptor("write_contract"),
        },
        project_root=tmp_path / "repo",
        workspace=workspace,
        routing=ModelRouting(model="qwen-plus", effort="medium"),
        budget=BudgetGrantRuntime(limit_usd=1.0, degradation_chain=()),
        attempt=1,
    )

    assert prepared.request.role == "coder"
    assert prepared.request.workspace == str(workspace)
    assert prepared.request.tools == ("read_file", "write_file")
    assert "write_contract" not in prepared.request.tools
    assert prepared.mount_paths == ("/workspace/src", "/packages/contracts")
    assert [m.mode for m in prepared.request.mounts] == ["rw", "ro"]


def test_prepare_rejects_zero_attempt(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="attempt"):
        prepare_spawn_request(
            packet_id=PacketId("wp-abcdef"),
            role_spec=role_spec(),
            tool_registry={
                "read_file": ToolDescriptor("read_file"),
                "write_file": ToolDescriptor("write_file"),
            },
            project_root=tmp_path / "repo",
            workspace=tmp_path / "workers" / "wp-abcdef",
            routing=ModelRouting(model="qwen-plus", effort="medium"),
            budget=BudgetGrantRuntime(limit_usd=1.0, degradation_chain=()),
            attempt=0,
        )
