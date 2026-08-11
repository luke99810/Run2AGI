"""Build the minimum task-intent context for a worker attempt."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from codentum_contracts.interfaces import MountSpec, SpawnRequest
from codentum_contracts.state import WorkPacket
from pydantic import ValidationError

from .assemble import ContextAssemblyError, ContextCandidate

__all__ = [
    "DEFAULT_INTENT_CONTEXT_CHAR_BUDGET",
    "PACKET_INTENT_REF",
    "packet_intent_candidate",
]

PACKET_INTENT_REF = "packet-intent"
DEFAULT_INTENT_CONTEXT_CHAR_BUDGET = 4000


def packet_intent_candidate(req: SpawnRequest, *, repo_root: Path | str) -> ContextCandidate:
    """Return a required context slice describing what this packet is for.

    This is deliberately a Harness-side adapter, not a contract change. If a
    WorkPacket file exists, it is the source. Otherwise the frozen SpawnRequest
    still provides a minimal fallback so model prompts never render with an empty
    Visible Context section.
    """
    packet_path = Path(repo_root) / ".codentum" / "packets" / f"{req.packet_id}.json"
    if packet_path.exists():
        packet = _load_workpacket(packet_path)
        if packet.id != req.packet_id:
            raise ContextAssemblyError(
                f"packet intent file id {packet.id!r} does not match SpawnRequest {req.packet_id!r}"
            )
        return ContextCandidate(
            ref=PACKET_INTENT_REF,
            artifact_path=f".codentum/packets/{req.packet_id}.json",
            text=_render_workpacket_intent(packet),
            summary=_render_workpacket_summary(packet),
            required=True,
            priority=0,
        )

    return ContextCandidate(
        ref=PACKET_INTENT_REF,
        artifact_path=f".codentum/runtime/{req.packet_id}/spawn-request",
        text=_render_spawn_request_intent(req),
        summary=_render_spawn_request_summary(req),
        required=True,
        priority=0,
    )


def _load_workpacket(path: Path) -> WorkPacket:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return WorkPacket.model_validate(raw)
    except OSError as exc:
        raise ContextAssemblyError(f"cannot read WorkPacket for task intent: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ContextAssemblyError(f"WorkPacket task-intent source is not JSON: {path}") from exc
    except ValidationError as exc:
        raise ContextAssemblyError(f"WorkPacket task-intent source failed schema validation: {path}") from exc


def _render_workpacket_intent(packet: WorkPacket) -> str:
    lines = [
        "# Packet Intent",
        "",
        "Source: .codentum/packets/<packet_id>.json",
        "Note: frozen WorkPacket has no natural-language task-description field; "
        "this is the minimum actionable intent derived from existing contract fields.",
        "",
        "## Minimum Task",
        "",
        f"- Complete WorkPacket `{packet.id}` as role `{packet.role}`.",
        f"- Packet kind: `{packet.kind}`.",
        "- Produce evidence that satisfies the acceptance predicate below.",
        "- Treat ownsPaths as the writable task surface and readsPaths as read-only context.",
        "",
        "## Paths",
        "",
        *_list("ownsPaths", packet.ownsPaths),
        *_list("readsPaths", packet.readsPaths),
        *_list("deps", packet.deps),
        "",
        "## Acceptance",
        "",
        f"- kind: {packet.acceptance.kind}",
        f"- predicate: {packet.acceptance.predicate}",
        f"- authoredBy: {packet.acceptance.authoredBy}",
        *([] if packet.acceptance.threshold is None else [f"- threshold: {packet.acceptance.threshold:g}"]),
        "",
        "## Provenance",
        "",
        f"- createdBy: {packet.provenance.createdBy}",
        f"- createdAt: {packet.provenance.createdAt}",
        *([] if packet.provenance.parent is None else [f"- parent: {packet.provenance.parent}"]),
        "",
    ]
    return "\n".join(lines)


def _render_workpacket_summary(packet: WorkPacket) -> str:
    return (
        f"Packet {packet.id}: {packet.kind} for role {packet.role}; "
        f"acceptance={packet.acceptance.kind}:{packet.acceptance.predicate}; "
        f"owns={','.join(packet.ownsPaths) or '(none)'}"
    )


def _render_spawn_request_intent(req: SpawnRequest) -> str:
    lines = [
        "# Packet Intent",
        "",
        "Source: SpawnRequest fallback",
        "Note: .codentum/packets/<packet_id>.json was not found. This fallback prevents "
        "an empty model prompt but does not replace a real task description.",
        "",
        "## Minimum Task",
        "",
        f"- Execute packet `{req.packet_id}` as role `{req.role}`.",
        "- Produce evidence for the harness to inspect.",
        "- Use the visible tools, mounts, routing and budget listed in this prompt.",
        "",
        "## Runtime Surface",
        "",
        *_list_mounts(req.mounts),
        f"- model: {req.routing.model}",
        f"- effort: {req.routing.effort}",
        f"- budget_cny: {req.budget.limit_cny:g}",
        "",
    ]
    return "\n".join(lines)


def _render_spawn_request_summary(req: SpawnRequest) -> str:
    writable = sorted(m.mount_path for m in req.mounts if m.mode == "rw")
    return (
        f"Packet {req.packet_id}: runtime fallback for role {req.role}; "
        f"rw_mounts={','.join(writable) or '(none)'}"
    )


def _list(label: str, values: tuple[object, ...]) -> list[str]:
    if not values:
        return [f"- {label}: (none)"]
    return [f"- {label}: {item}" for item in values]


def _list_mounts(mounts: Sequence[MountSpec]) -> list[str]:
    if not mounts:
        return ["- mounts: (none)"]
    return [f"- mount: {mount.mode} {mount.mount_path} <= {mount.host_path}" for mount in mounts]
