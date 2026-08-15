"""Mirrored Worker evidence writes.

Workers still write their full evidence inside the isolated workspace.  When a
project state directory is supplied, the same bytes are mirrored into the
authoritative project `.codentum/evidence/<worker>/` tree for desktop and audit
readers.
"""

from __future__ import annotations

from pathlib import Path
from shutil import copy2

__all__ = ["MirroredEvidence"]


class MirroredEvidence:
    """Write evidence to the worker workspace and optionally mirror it."""

    def __init__(self, primary_dir: Path | str, mirror_dir: Path | str | None = None) -> None:
        self.primary_dir = Path(primary_dir)
        self.mirror_dir = None if mirror_dir is None else Path(mirror_dir)

    def write_text(self, relative_path: str, text: str) -> None:
        for target in self._targets(relative_path):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")

    def append_text(self, relative_path: str, text: str) -> None:
        for target in self._targets(relative_path):
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as handle:
                handle.write(text)

    def mirror_file(self, relative_path: str) -> None:
        if self.mirror_dir is None:
            return
        source = self.primary_dir / self._safe_relative(relative_path)
        target = self.mirror_dir / self._safe_relative(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        copy2(source, target)

    def mirror_tree(self, relative_path: str) -> None:
        if self.mirror_dir is None:
            return
        source_root = self.primary_dir / self._safe_relative(relative_path)
        if not source_root.exists():
            return
        for source in sorted(path for path in source_root.rglob("*") if path.is_file()):
            target = self.mirror_dir / source.relative_to(self.primary_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            copy2(source, target)

    def _targets(self, relative_path: str) -> tuple[Path, ...]:
        relative = self._safe_relative(relative_path)
        primary = self.primary_dir / relative
        if self.mirror_dir is None:
            return (primary,)
        mirror = self.mirror_dir / relative
        if primary == mirror:
            return (primary,)
        return (primary, mirror)

    @staticmethod
    def _safe_relative(relative_path: str) -> Path:
        path = Path(relative_path.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"evidence path must be relative and stay inside evidence dir: {relative_path}")
        return path
