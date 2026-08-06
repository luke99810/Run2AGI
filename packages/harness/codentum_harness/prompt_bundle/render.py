"""Render deterministic prompt bundles for worker model execution."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from codentum_contracts.interfaces import ModelMessage, ModelRequest, SpawnRequest
from codentum_contracts.state import Effort, RoleSpec

from codentum_harness.context_broker import ContextBundle

__all__ = [
    "PromptBundleError",
    "WorkerPromptBundle",
    "assemble_worker_prompt_bundle",
    "write_worker_prompt_bundle",
]


class PromptBundleError(ValueError):
    """A worker prompt bundle cannot be rendered safely."""


@dataclass(frozen=True, slots=True)
class WorkerPromptBundle:
    """Stable model input package for one worker attempt."""

    system: str
    user: str
    digest: str

    def to_model_request(self, *, effort: Effort | None = None) -> ModelRequest:
        return ModelRequest(
            system=self.system,
            messages=(ModelMessage(role="user", content=self.user),),
            effort=effort,
        )


def assemble_worker_prompt_bundle(
    request: SpawnRequest,
    role_spec: RoleSpec,
    *,
    context: ContextBundle | None = None,
) -> WorkerPromptBundle:
    """Render model input from already-enforced worker constraints."""

    if request.role != role_spec.id:
        raise PromptBundleError(f"request role {request.role!r} does not match RoleSpec[{role_spec.id}]")
    if context is not None and context.role != role_spec.id:
        raise PromptBundleError(f"context role {context.role!r} does not match RoleSpec[{role_spec.id}]")

    system = _render_system(role_spec)
    user = _render_user(request, context=context)
    digest = _digest({"schema_version": 1, "system": system, "user": user})
    return WorkerPromptBundle(system=system, user=user, digest=digest)


def write_worker_prompt_bundle(
    *,
    request: SpawnRequest,
    role_spec: RoleSpec,
    evidence_dir: Path | str,
    context: ContextBundle | None = None,
) -> WorkerPromptBundle:
    """Write system/user prompts and a stable manifest under evidence_dir."""

    bundle = assemble_worker_prompt_bundle(request, role_spec, context=context)
    prompt_dir = Path(evidence_dir) / "prompt"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    (prompt_dir / "system.md").write_text(bundle.system, encoding="utf-8")
    (prompt_dir / "user.md").write_text(bundle.user, encoding="utf-8")

    manifest = {
        "schema_version": 1,
        "digest": bundle.digest,
        "packet_id": request.packet_id,
        "role": request.role,
        "attempt": request.attempt,
        "system_path": "system.md",
        "user_path": "user.md",
        "context_refs": list(context.refs) if context is not None else [],
    }
    (prompt_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return bundle


def _render_system(role_spec: RoleSpec) -> str:
    lines = [
        "# Codentum Worker",
        "",
        f"Role: {role_spec.id}",
        f"Summary: {role_spec.summary or '(none)'}",
        "",
        "The harness has already enforced mounts, tool visibility, and context visibility "
        "before this prompt.",
        "Treat the lists below as the execution surface you can actually see and use.",
        "If required information is absent from the visible context, report the blocker instead of guessing.",
        "",
        "Hard constraints live in RoleSpec, mounts, tool surface, and gates. "
        "This prompt is orientation only.",
        "",
    ]
    return "\n".join(lines)


def _render_user(request: SpawnRequest, *, context: ContextBundle | None) -> str:
    lines = [
        "# Worker Input",
        "",
        "## Execution",
        "",
        f"- packet_id: {request.packet_id}",
        f"- role: {request.role}",
        f"- attempt: {request.attempt}",
        f"- workspace: {request.workspace}",
        f"- model: {request.routing.model}",
        f"- effort: {request.routing.effort}",
        f"- budget_usd: {request.budget.limit_usd:g}",
        "",
        "## Visible Tools",
        "",
        *_bullet_list(request.tools),
        "",
        "## Mounts",
        "",
        *_render_mounts(request),
        "",
        "## Visible Context",
        "",
        *_render_context(context),
        "",
        "## Output",
        "",
        "- Make changes only through the provided execution surface.",
        "- Leave enough evidence for the harness and downstream reviewer to inspect the result.",
        "- Prefer explicit blockers over silent assumptions when visible context is insufficient.",
        "",
    ]
    return "\n".join(lines)


def _bullet_list(values: Sequence[str]) -> list[str]:
    items = tuple(values)
    if not items:
        return ["- (none)"]
    return [f"- {item}" for item in items]


def _render_mounts(request: SpawnRequest) -> list[str]:
    if not request.mounts:
        return ["- (none)"]
    return [
        f"- {mount.mode}: {mount.mount_path} <= {mount.host_path}"
        for mount in sorted(request.mounts, key=lambda item: (item.mount_path, item.host_path, item.mode))
    ]


def _render_context(context: ContextBundle | None) -> list[str]:
    if context is None or not context.slices:
        return ["- (none)"]

    lines: list[str] = []
    for slice_ in context.slices:
        lines.extend(
            [
                f"### {slice_.ref}",
                "",
                f"- path: {slice_.artifact_path}",
                f"- mode: {slice_.mode}",
                f"- original_chars: {slice_.original_chars}",
                "",
                _fenced(slice_.text),
                "",
            ]
        )
    if context.denied:
        lines.extend(["## Denied Context", ""])
        lines.extend(
            f"- {item.ref}: {item.artifact_path} ({item.reason})"
            for item in sorted(context.denied, key=lambda item: (item.ref, item.artifact_path))
        )
        lines.append("")
    if context.omitted:
        lines.extend(["## Omitted Context", ""])
        lines.extend(
            f"- {item.ref}: {item.artifact_path} ({item.reason})"
            for item in sorted(context.omitted, key=lambda item: (item.ref, item.artifact_path))
        )
        lines.append("")
    return lines


def _fenced(text: str) -> str:
    longest = max((len(match.group(0)) for match in re.finditer(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}\n{text}\n{fence}"


def _digest(value: dict[str, object]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"
