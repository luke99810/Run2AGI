from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from shutil import which

import pytest
from codentum_contracts import (
    BudgetGrantRuntime,
    ModelRouting,
    PacketId,
    SpawnRequest,
    WorkerCompleted,
    WorkerFailed,
)
from codentum_harness.runner import CommandRunner


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


def test_command_runner_completes_and_writes_evidence(git_repo: Path) -> None:
    runner = CommandRunner(
        (
            sys.executable,
            "-c",
            "from pathlib import Path; Path('worker.txt').write_text('ran\\n'); print('ok')",
        )
    )

    outcome = runner(request(git_repo))

    assert isinstance(outcome, WorkerCompleted)
    assert outcome.evidence == ("file:runner/result.json",)
    assert "worker.txt" in outcome.touched_paths

    runner_dir = git_repo / ".codentum" / "evidence" / "wp-abcdef-attempt-1" / "runner"
    result = json.loads((runner_dir / "result.json").read_text(encoding="utf-8"))
    assert result["exit_code"] == 0
    assert result["status"] == "completed"
    assert (runner_dir / "stdout.txt").read_text(encoding="utf-8") == "ok\n"


def test_command_runner_failure_returns_failed_outcome(git_repo: Path) -> None:
    runner = CommandRunner((sys.executable, "-c", "import sys; print('bad', file=sys.stderr); sys.exit(7)"))

    outcome = runner(request(git_repo))

    assert isinstance(outcome, WorkerFailed)
    assert outcome.reason_code == "model_error"
    runner_dir = git_repo / ".codentum" / "evidence" / "wp-abcdef-attempt-1" / "runner"
    result = json.loads((runner_dir / "result.json").read_text(encoding="utf-8"))
    assert result["exit_code"] == 7
    assert (runner_dir / "stderr.txt").read_text(encoding="utf-8") == "bad\n"


def test_command_runner_renders_safe_placeholders(git_repo: Path) -> None:
    runner = CommandRunner(
        (
            sys.executable,
            "-c",
            "import sys; print(sys.argv[1]); print(sys.argv[2])",
            "{packet_id}",
            "{workspace}",
        )
    )

    outcome = runner(request(git_repo))

    assert isinstance(outcome, WorkerCompleted)
    runner_dir = git_repo / ".codentum" / "evidence" / "wp-abcdef-attempt-1" / "runner"
    assert (runner_dir / "stdout.txt").read_text(encoding="utf-8") == f"wp-abcdef\n{git_repo}\n"


def test_command_runner_exposes_prompt_bundle_placeholders(git_repo: Path) -> None:
    prompt_dir = git_repo / ".codentum" / "evidence" / "wp-abcdef-attempt-1" / "prompt"
    prompt_dir.mkdir(parents=True)
    (prompt_dir / "user.md").write_text("do the work\n", encoding="utf-8")

    runner = CommandRunner(
        (
            sys.executable,
            "-c",
            "from pathlib import Path; import sys; print(Path(sys.argv[1]).read_text(), end='')",
            "{user_prompt}",
        )
    )

    outcome = runner(request(git_repo))

    assert isinstance(outcome, WorkerCompleted)
    runner_dir = git_repo / ".codentum" / "evidence" / "wp-abcdef-attempt-1" / "runner"
    assert (runner_dir / "stdout.txt").read_text(encoding="utf-8") == "do the work\n"


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
