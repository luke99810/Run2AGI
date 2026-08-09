from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from shutil import which
from types import SimpleNamespace
from typing import Any, ClassVar

import codentum_harness.model_gateway.openai_compatible as openai_provider
import pytest
from codentum_contracts import (
    BudgetGrantRuntime,
    ModelGateway,
    ModelRouting,
    PacketId,
    SpawnRequest,
    Usage,
    WorkerCompleted,
)
from codentum_contracts.interfaces import ModelMessage, ModelRequest
from codentum_harness.runtime import (
    LocalWorkerRuntimeConfig,
    ModelGatewayConfig,
    RunnerConfig,
    TokenPricingConfig,
    build_local_worker_runtime,
    build_model_gateway,
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
        budget=BudgetGrantRuntime(limit_cny=1.0, degradation_chain=()),
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


def test_build_model_gateway_builds_bailian_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    FakeOpenAISDK.instances.clear()
    monkeypatch.setattr(openai_provider, "import_module", fake_openai_import_module)
    config = ModelGatewayConfig.bailian(
        pricing={"qwen-plus": TokenPricingConfig(1.0, 2.0)},
    )

    gateway = build_model_gateway(config)
    response = asyncio.run(_invoke_model_gateway(gateway))

    assert response.usage == Usage(
        cost_cny=0.000003,
        input_tokens=1,
        output_tokens=1,
        cached_input_tokens=0,
    )
    assert FakeOpenAISDK.instances[0].api_key == "test-key"
    assert FakeOpenAISDK.instances[0].base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_build_runner_rejects_model_gateway_without_gateway_config() -> None:
    with pytest.raises(ValueError, match="gateway config"):
        build_runner(RunnerConfig(kind="model_gateway"))


async def _spawn_and_settle(runtime: object, req: SpawnRequest) -> WorkerCompleted:
    assert hasattr(runtime, "spawn")
    assert hasattr(runtime, "settle")
    handle = await runtime.spawn(req)
    outcome = await runtime.settle(handle)
    assert isinstance(outcome, WorkerCompleted)
    return outcome


async def _invoke_model_gateway(gateway: ModelGateway) -> Any:
    session = await gateway.open("coder", ModelRouting(model="qwen-plus", effort="medium"), 1.0)
    try:
        return await session.invoke(
            ModelRequest(
                system="system",
                messages=(ModelMessage(role="user", content="hello"),),
            )
        )
    finally:
        await session.close()


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


def fake_openai_import_module(name: str) -> SimpleNamespace:
    assert name == "openai"
    return SimpleNamespace(AsyncOpenAI=FakeOpenAISDK)


class FakeOpenAISDK:
    instances: ClassVar[list[FakeOpenAISDK]] = []

    def __init__(self, *, api_key: str, base_url: str, timeout: float) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.chat = SimpleNamespace(completions=FakeOpenAICompletions())
        self.instances.append(self)


class FakeOpenAICompletions:
    async def create(self, **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            choices=(
                SimpleNamespace(
                    message=SimpleNamespace(content="done", tool_calls=None),
                    finish_reason="stop",
                ),
            ),
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )
