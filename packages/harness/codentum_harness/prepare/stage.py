"""Prepare a single worker execution from RoleSpec-derived constraints."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from codentum_contracts.interfaces import BudgetGrantRuntime, SpawnRequest
from codentum_contracts.state import ModelRouting, PacketId, RoleSpec

from codentum_harness.context_broker import ContextBundle, ContextCandidate, assemble_context_bundle
from codentum_harness.mounts import derive_mounts
from codentum_harness.tool_surface import ToolDescriptor, derive_tool_surface

__all__ = [
    "PreparedExecution",
    "prepare_spawn_request",
]


@dataclass(frozen=True, slots=True)
class PreparedExecution:
    """Prepared metadata for one worker attempt."""

    request: SpawnRequest
    role_spec: RoleSpec
    tools: tuple[str, ...]
    mount_paths: tuple[str, ...]
    context: ContextBundle | None


def prepare_spawn_request(
    *,
    packet_id: PacketId,
    role_spec: RoleSpec,
    tool_registry: dict[str, ToolDescriptor],
    project_root: Path | str,
    workspace: Path | str,
    routing: ModelRouting,
    budget: BudgetGrantRuntime,
    attempt: int,
    context_candidates: Sequence[ContextCandidate] | None = None,
    context_char_budget: int | None = None,
) -> PreparedExecution:
    """Build a SpawnRequest without invoking the model or control-plane."""
    if attempt < 1:
        raise ValueError("attempt must start at 1")
    if context_candidates is None and context_char_budget is not None:
        raise ValueError("context_candidates are required when context_char_budget is provided")
    if context_candidates is not None and context_char_budget is None:
        raise ValueError("context_char_budget is required when context_candidates are provided")

    workspace_path = Path(workspace)
    tool_surface = derive_tool_surface(role_spec, tool_registry)
    mounts = derive_mounts(
        role_spec,
        project_root=project_root,
        workspace_root=workspace_path,
    )
    context = (
        assemble_context_bundle(role_spec, candidates=context_candidates, char_budget=context_char_budget)
        if context_candidates is not None and context_char_budget is not None
        else None
    )
    request = SpawnRequest(
        packet_id=packet_id,
        role=role_spec.id,
        mounts=mounts,
        tools=tool_surface.tool_names,
        routing=routing,
        budget=budget,
        workspace=str(workspace_path),
        attempt=attempt,
    )
    return PreparedExecution(
        request=request,
        role_spec=role_spec,
        tools=tool_surface.tool_names,
        mount_paths=tuple(m.mount_path for m in mounts),
        context=context,
    )
