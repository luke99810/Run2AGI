from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from shutil import which

import pytest
from codentum_contracts import BudgetGrantRuntime, ModelRouting, PacketId, SpawnRequest, WorkerCompleted
from codentum_harness.runtime import (
    LocalWorkerRuntimeConfig,
    RunnerConfig,
    build_local_worker_runtime,
    build_runner,
)


@pytest.fixture
def git_repo(tmp_path: Path) -> Iterator[Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(repo, "init")
    run_git(repo, "config", "user.name", "tester")
    run_git(repo, "config", "user.email", "tester@example.com")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    run_git(repo, "add", "README.md")
    run_git(repo, "commit", "-m", "initial")
    yield repo


def request(workspace: Path) -> SpawnRequest:
    return SpawnRequest(
        packet_id=PacketId("wp-abcdef"),
        role="coder",
        mounts=(),
        tools=("read_file", "write_file"),
        routing=ModelRouting(model="qwen-plus", effort="medium"),
        budget=BudgetGrantRuntime(limit_usd=1.0, degradation_chain=()),
        workspace=str(workspace),
        attempt=1,
    )


def test_build_runner_returns_none_when_runner_is_disabled() -> None:
    assert build_runner(None) is None
    assert build_runner(RunnerConfig()) is None


def test_build_runner_rejects_invalid_command_config() -> None:
    with pytest.raises(ValueError, match="non-empty command"):
        build_runner(RunnerConfig(kind="command"))

    with pytest.raises(ValueError, match="timeout_seconds"):
        build_runner(RunnerConfig.command_runner((sys.executable,), timeout_seconds=0))


def test_build_local_worker_runtime_wires_command_runner(git_repo: Path, tmp_path: Path) -> None:
    workspace = tmp_path / "workers" / "wp-abcdef"
    config = LocalWorkerRuntimeConfig(
        repo_root=git_repo,
        runner=RunnerConfig.command_runner(
            (
                sys.executable,
                "-c",
                "from pathlib import Path; Path('from_factory.txt').write_text('ok\\n')",
            )
        ),
    )

    runtime = build_local_worker_runtime(config)
    outcome = asyncio.run(_spawn_and_settle(runtime, request(workspace)))

    assert outcome.status == "completed"
    assert "from_factory.txt" in outcome.touched_paths
    assert (workspace / "from_factory.txt").read_text(encoding="utf-8") == "ok\n"


async def _spawn_and_settle(runtime: object, req: SpawnRequest) -> WorkerCompleted:
    assert hasattr(runtime, "spawn")
    assert hasattr(runtime, "settle")
    handle = await runtime.spawn(req)
    outcome = await runtime.settle(handle)
    assert isinstance(outcome, WorkerCompleted)
    return outcome


def run_git(cwd: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603 - fixed executable and argument list, no shell.
        [_git(), "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


def _git() -> str:
    exe = which("git")
    if exe is None:
        raise RuntimeError("git executable not found")
    return exe
