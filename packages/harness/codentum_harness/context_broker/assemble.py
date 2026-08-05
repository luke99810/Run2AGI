"""Deterministic Context Broker primitives.

The P0 broker intentionally avoids retrieval infrastructure. It receives
already-selected candidate slices, applies RoleSpec visibility first, then
uses a deterministic degradation chain: full text -> summary -> reference.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Literal

from codentum_contracts.state import RoleId, RoleSpec

__all__ = [
    "ContextAssemblyError",
    "ContextBudgetError",
    "ContextBundle",
    "ContextCandidate",
    "ContextSlice",
    "ContextVisibilityError",
    "DeniedContext",
    "OmittedContext",
    "assemble_context_bundle",
]

ContextMode = Literal["full", "summary", "reference"]


class ContextAssemblyError(ValueError):
    """A context bundle cannot be assembled safely."""


class ContextVisibilityError(ContextAssemblyError):
    """Required context is hidden by the role visibility policy."""


class ContextBudgetError(ContextAssemblyError):
    """Required context cannot fit the declared budget degradation chain."""


@dataclass(frozen=True, slots=True)
class ContextCandidate:
    """A candidate artifact that may become a context slice."""

    ref: str
    artifact_path: str
    text: str
    required: bool = False
    summary: str | None = None
    priority: int = 100


@dataclass(frozen=True, slots=True)
class ContextSlice:
    """A concrete slice included in the model context."""

    ref: str
    artifact_path: str
    mode: ContextMode
    text: str
    original_chars: int

    @property
    def chars(self) -> int:
        return len(self.text)


@dataclass(frozen=True, slots=True)
class DeniedContext:
    """A candidate hidden by RoleSpec.invisible."""

    ref: str
    artifact_path: str
    reason: Literal["invisible"]


@dataclass(frozen=True, slots=True)
class OmittedContext:
    """A non-required candidate omitted because even a reference could not fit."""

    ref: str
    artifact_path: str
    reason: Literal["budget_exhausted"]


@dataclass(frozen=True, slots=True)
class ContextBundle:
    """Deterministic context bundle for one role execution."""

    role: RoleId
    slices: tuple[ContextSlice, ...]
    denied: tuple[DeniedContext, ...]
    omitted: tuple[OmittedContext, ...]
    char_total: int
    degraded: bool

    @property
    def refs(self) -> tuple[str, ...]:
        return tuple(slice_.ref for slice_ in self.slices)


def assemble_context_bundle(
    spec: RoleSpec,
    *,
    candidates: Sequence[ContextCandidate],
    char_budget: int,
) -> ContextBundle:
    """Assemble role-visible context without truncation or model calls."""
    if char_budget < 1:
        raise ContextBudgetError("context char_budget must be positive")

    _reject_duplicate_refs(candidates)

    patterns = tuple(_normalize_pattern(pattern) for pattern in spec.invisible or ())
    slices: list[ContextSlice] = []
    denied: list[DeniedContext] = []
    omitted: list[OmittedContext] = []
    spent = 0
    degraded = False

    for candidate in sorted(candidates, key=lambda item: (item.priority, item.ref, item.artifact_path)):
        path = _normalize_artifact_path(candidate.artifact_path)
        if _is_invisible(path, patterns):
            if candidate.required:
                raise ContextVisibilityError(
                    f"required context {candidate.ref!r} is invisible to RoleSpec[{spec.id}]"
                )
            denied.append(DeniedContext(ref=candidate.ref, artifact_path=path, reason="invisible"))
            continue

        remaining = char_budget - spent
        selected = _select_variant(candidate, artifact_path=path, remaining=remaining)
        if selected is None:
            if candidate.required:
                raise ContextBudgetError(
                    f"required context {candidate.ref!r} cannot fit budget {char_budget}"
                )
            omitted.append(OmittedContext(ref=candidate.ref, artifact_path=path, reason="budget_exhausted"))
            degraded = True
            continue

        slices.append(selected)
        spent += selected.chars
        degraded = degraded or selected.mode != "full"

    return ContextBundle(
        role=spec.id,
        slices=tuple(slices),
        denied=tuple(denied),
        omitted=tuple(omitted),
        char_total=spent,
        degraded=degraded,
    )


def _reject_duplicate_refs(candidates: Sequence[ContextCandidate]) -> None:
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate.ref:
            raise ContextAssemblyError("context candidate ref must not be empty")
        if candidate.ref in seen:
            raise ContextAssemblyError(f"duplicate context candidate ref: {candidate.ref!r}")
        seen.add(candidate.ref)


def _select_variant(
    candidate: ContextCandidate,
    *,
    artifact_path: str,
    remaining: int,
) -> ContextSlice | None:
    for mode, text in _variants(candidate, artifact_path=artifact_path):
        if len(text) <= remaining:
            return ContextSlice(
                ref=candidate.ref,
                artifact_path=artifact_path,
                mode=mode,
                text=text,
                original_chars=len(candidate.text),
            )
    return None


def _variants(
    candidate: ContextCandidate,
    *,
    artifact_path: str,
) -> tuple[tuple[ContextMode, str], ...]:
    variants: list[tuple[ContextMode, str]] = [("full", candidate.text)]
    if candidate.summary is not None:
        variants.append(("summary", candidate.summary))
    variants.append(("reference", _reference_text(candidate, artifact_path=artifact_path)))
    return tuple(variants)


def _reference_text(candidate: ContextCandidate, *, artifact_path: str) -> str:
    return f"ref:{candidate.ref}\npath:{artifact_path}\n"


def _is_invisible(path: str, patterns: tuple[str, ...]) -> bool:
    return any(_matches(pattern, path) for pattern in patterns)


def _matches(pattern: str, path: str) -> bool:
    if fnmatchcase(path, pattern):
        return True
    if pattern.endswith("/**"):
        prefix = pattern[:-3]
        return path == prefix or path.startswith(f"{prefix}/")
    return False


def _normalize_pattern(pattern: str) -> str:
    raw = pattern.replace("\\", "/").strip()
    if not raw:
        raise ContextAssemblyError("empty invisible path pattern")
    if raw.startswith("/"):
        raise ContextAssemblyError(f"absolute invisible paths are not allowed: {pattern!r}")
    return _normalize_segments(raw, source=pattern)


def _normalize_artifact_path(path: str) -> str:
    raw = path.replace("\\", "/").strip()
    if not raw:
        raise ContextAssemblyError("context artifact_path must not be empty")
    if raw.startswith("/"):
        raise ContextAssemblyError(f"absolute context artifact_path is not allowed: {path!r}")
    return _normalize_segments(raw, source=path)


def _normalize_segments(raw: str, *, source: str) -> str:
    segments: list[str] = []
    for segment in raw.split("/"):
        if segment in {"", "."}:
            continue
        if segment == "..":
            raise ContextAssemblyError(f"parent traversal is not allowed in context paths: {source!r}")
        segments.append(segment)
    if not segments:
        raise ContextAssemblyError(f"context path has no usable segment: {source!r}")
    return "/".join(segments)
