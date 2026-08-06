"""Git worktree isolation for local workers."""

from __future__ import annotations

import subprocess
from pathlib import Path
from shutil import which

__all__ = [
    "GitWorktreeManager",
    "WorktreeIsolationError",
]


class WorktreeIsolationError(RuntimeError):
    """A worker workspace cannot be prepared safely."""


class GitWorktreeManager:
    """Create one isolated Git worktree per worker attempt."""

    def __init__(self, repo_root: Path | str) -> None:
        self.repo_root = self._resolve_git_root(Path(repo_root))

    def create(self, workspace: Path | str, *, ref: str = "HEAD") -> Path:
        """Create an isolated worktree at `workspace`.

        The workspace must live outside the source repository. If it were nested
        under the repo, worker writes could leak into the controller checkout and
        break the physical isolation this layer is supposed to provide.
        """
        path = Path(workspace).expanduser()
        if not path.is_absolute():
            path = self.repo_root / path
        path = path.resolve()
        self._reject_inside_repo(path)
        if path.exists() and any(path.iterdir()):
            raise WorktreeIsolationError(f"worker workspace is not empty: {path}")

        path.parent.mkdir(parents=True, exist_ok=True)
        self._git(["worktree", "add", "--detach", str(path), ref])
        return Path(str(path).replace("\\", "/"))

    def remove(self, workspace: Path | str) -> None:
        """Remove a worker worktree."""
        path = Path(workspace).expanduser().resolve()
        self._git(["worktree", "remove", "--force", str(path)])

    def _reject_inside_repo(self, workspace: Path) -> None:
        if workspace == self.repo_root or self.repo_root in workspace.parents:
            raise WorktreeIsolationError(
                f"worker workspace must be outside repo root {self.repo_root}: {workspace}"
            )

    def _git(self, args: list[str]) -> str:
        try:
            return subprocess.run(  # noqa: S603 - fixed executable and argument list, no shell.
                [_git(), "-C", str(self.repo_root), *args],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            ).stdout
        except FileNotFoundError as exc:
            raise WorktreeIsolationError("git executable not found") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout).strip()
            raise WorktreeIsolationError(detail or f"git command failed: {' '.join(args)}") from exc

    @classmethod
    def _resolve_git_root(cls, path: Path) -> Path:
        if not path.exists():
            raise WorktreeIsolationError(f"not a git repository: {path}")

        try:
            out = subprocess.run(  # noqa: S603 - fixed executable and argument list, no shell.
                [_git(), "-C", str(path), "rev-parse", "--show-toplevel"],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            ).stdout
        except FileNotFoundError as exc:
            raise WorktreeIsolationError("git executable not found") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout).strip()
            raise WorktreeIsolationError(detail or f"not a git repository: {path}") from exc
        return Path(out.strip()).resolve()


def _git() -> str:
    exe = which("git")
    if exe is None:
        raise WorktreeIsolationError("git executable not found")
    return exe
