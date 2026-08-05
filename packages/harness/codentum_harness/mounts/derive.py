"""Derive runtime mounts from RoleSpec reads/writes/invisible."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from codentum_contracts.interfaces import MountSpec
from codentum_contracts.state import RoleSpec

__all__ = [
    "MountPlanError",
    "derive_mounts",
]

MountMode = Literal["ro", "rw"]


class MountPlanError(ValueError):
    """RoleSpec cannot be converted into a safe mount plan."""


@dataclass(frozen=True, slots=True)
class _MountEntry:
    pattern: str
    prefix: str
    mode: MountMode


def derive_mounts(
    spec: RoleSpec,
    *,
    project_root: Path | str,
    workspace_root: Path | str | None = None,
) -> tuple[MountSpec, ...]:
    """Derive deterministic MountSpec entries from one RoleSpec.

    `writes` become rw mounts, `reads` become ro mounts, and `invisible` must not
    overlap any mounted prefix. Without an exclude-aware MountSpec, mounting a
    parent while declaring a child invisible would silently expose the child.
    """
    repo_root = Path(project_root)
    worktree_root = Path(workspace_root) if workspace_root is not None else repo_root / "workspace"

    entries = (
        *(_entry(pattern, "rw") for pattern in spec.writes),
        *(_entry(pattern, "ro") for pattern in spec.reads),
    )

    _reject_duplicate_mounts(spec, entries)
    _reject_read_write_overlap(spec, entries)
    _reject_invisible_overlap(spec, entries)

    return tuple(_to_mount(entry, project_root=repo_root, workspace_root=worktree_root) for entry in entries)


def _entry(pattern: str, mode: MountMode) -> _MountEntry:
    return _MountEntry(pattern=pattern, prefix=_static_prefix(pattern), mode=mode)


def _to_mount(entry: _MountEntry, *, project_root: Path, workspace_root: Path) -> MountSpec:
    host = _host_path(entry.prefix, project_root=project_root, workspace_root=workspace_root)
    return MountSpec(
        host_path=str(host),
        mount_path=f"/{entry.prefix}",
        mode=entry.mode,
    )


def _host_path(prefix: str, *, project_root: Path, workspace_root: Path) -> Path:
    first, _, rest = prefix.partition("/")
    if first == "workspace":
        return workspace_root / rest if rest else workspace_root
    return project_root / prefix


def _static_prefix(pattern: str) -> str:
    raw = pattern.replace("\\", "/").strip()
    if not raw:
        raise MountPlanError("empty path pattern")
    if raw.startswith("/"):
        raise MountPlanError(f"absolute paths are not allowed in RoleSpec mounts: {pattern!r}")

    segments: list[str] = []
    for segment in raw.split("/"):
        if segment in {"", "."}:
            continue
        if segment == "..":
            raise MountPlanError(f"parent traversal is not allowed in RoleSpec mounts: {pattern!r}")
        if _has_glob(segment):
            break
        segments.append(segment)

    if not segments:
        raise MountPlanError(f"path pattern has no static prefix: {pattern!r}")
    return "/".join(segments)


def _has_glob(segment: str) -> bool:
    return any(ch in segment for ch in "*?[")


def _reject_duplicate_mounts(spec: RoleSpec, entries: tuple[_MountEntry, ...]) -> None:
    seen: set[tuple[str, MountMode]] = set()
    for entry in entries:
        key = (entry.prefix, entry.mode)
        if key in seen:
            raise MountPlanError(
                f"RoleSpec[{spec.id}] declares duplicate {entry.mode} mount prefix {entry.prefix!r}"
            )
        seen.add(key)


def _reject_read_write_overlap(spec: RoleSpec, entries: tuple[_MountEntry, ...]) -> None:
    for i, left in enumerate(entries):
        for right in entries[i + 1 :]:
            if left.mode != right.mode and _overlaps(left.prefix, right.prefix):
                raise MountPlanError(
                    f"RoleSpec[{spec.id}] has ambiguous ro/rw mounts: "
                    f"{left.pattern!r} ({left.mode}) overlaps {right.pattern!r} ({right.mode})"
                )


def _reject_invisible_overlap(spec: RoleSpec, entries: tuple[_MountEntry, ...]) -> None:
    for invisible in spec.invisible or ():
        invisible_prefix = _static_prefix(invisible)
        for entry in entries:
            if _overlaps(invisible_prefix, entry.prefix):
                raise MountPlanError(
                    f"RoleSpec[{spec.id}] declares invisible path {invisible!r}, "
                    f"but mounted path {entry.pattern!r} would expose it"
                )


def _overlaps(left: str, right: str) -> bool:
    return left == right or left.startswith(f"{right}/") or right.startswith(f"{left}/")
