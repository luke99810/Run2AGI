"""Non-bypassable repository secret scanning primitives."""

from .scanner import (
    Finding,
    ScanReport,
    ScanUnavailable,
    scan_git_history,
    scan_repository,
    scan_text,
    scan_worktree,
)

__all__ = [
    "Finding",
    "ScanReport",
    "ScanUnavailable",
    "scan_git_history",
    "scan_repository",
    "scan_text",
    "scan_worktree",
]
