from __future__ import annotations

from pathlib import Path

import pytest
from codentum_contracts.state import RoleSpec
from codentum_harness.mounts import MountPlanError, derive_mounts


def spec(
    *,
    reads: tuple[str, ...] = (),
    writes: tuple[str, ...] = (),
    invisible: tuple[str, ...] = (),
) -> RoleSpec:
    return RoleSpec(
        id="coder",
        usesModel=True,
        writes=writes,
        reads=reads,
        invisible=invisible,
        tools=(),
        transitions=(),
    )


def test_reads_and_writes_become_ro_and_rw_mounts(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    worktree = tmp_path / "worktree"

    mounts = derive_mounts(
        spec(
            writes=("workspace/src/**",),
            reads=("packages/contracts/**", "tests/**"),
        ),
        project_root=repo,
        workspace_root=worktree,
    )

    by_path = {m.mount_path: m for m in mounts}
    assert by_path["/workspace/src"].host_path == str(worktree / "src")
    assert by_path["/workspace/src"].mode == "rw"
    assert by_path["/packages/contracts"].host_path == str(repo / "packages/contracts")
    assert by_path["/packages/contracts"].mode == "ro"
    assert by_path["/tests"].mode == "ro"


def test_invisible_paths_do_not_create_mounts(tmp_path: Path) -> None:
    mounts = derive_mounts(
        spec(
            writes=("workspace/src/**",),
            reads=("packages/contracts/**",),
            invisible=("evidence/coder-reasoning/**",),
        ),
        project_root=tmp_path / "repo",
        workspace_root=tmp_path / "worktree",
    )

    assert all("coder-reasoning" not in mount.mount_path for mount in mounts)


def test_same_prefix_cannot_be_both_read_and_write(tmp_path: Path) -> None:
    with pytest.raises(MountPlanError, match="ambiguous ro/rw"):
        derive_mounts(
            spec(
                writes=("packages/contracts/**",),
                reads=("packages/contracts/**",),
            ),
            project_root=tmp_path,
        )


def test_parent_child_read_write_overlap_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(MountPlanError, match="ambiguous ro/rw"):
        derive_mounts(
            spec(
                writes=("workspace/src/**",),
                reads=("workspace/src/generated/**",),
            ),
            project_root=tmp_path / "repo",
            workspace_root=tmp_path / "worktree",
        )


def test_invisible_path_under_mounted_parent_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(MountPlanError, match="would expose"):
        derive_mounts(
            spec(
                reads=("evidence/**",),
                invisible=("evidence/coder-reasoning/**",),
            ),
            project_root=tmp_path,
        )


def test_invalid_mount_patterns_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(MountPlanError, match="parent traversal"):
        derive_mounts(spec(reads=("../contracts/**",)), project_root=tmp_path)
