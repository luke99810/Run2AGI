from __future__ import annotations

import json
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from shutil import which
from typing import Any

import pytest
from codentum_contracts import (
    BudgetGrantRuntime,
    CostEstimate,
    CostLedger,
    ModelId,
    ModelResponse,
    ModelRouting,
    PacketId,
    RoleId,
    RoleSpec,
    SpawnRequest,
    Usage,
    WorkerCompleted,
    WorkerFailed,
)
from codentum_contracts.interfaces import ModelMessage, ModelRequest
from codentum_harness.prompt_bundle import write_worker_prompt_bundle
from codentum_harness.runner import ModelGatewayRunner


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


def role_spec() -> RoleSpec:
    return RoleSpec(
        id="coder",
        summary="implement packet",
        usesModel=True,
        writes=("workspace/**",),
        reads=("packages/contracts/**",),
        tools=("read_file", "write_file"),
        transitions=(),
    )


def test_model_gateway_runner_invokes_gateway_from_prompt_bundle(git_repo: Path) -> None:
    req = request(git_repo)
    evidence_root = git_repo / ".codentum" / "evidence" / "wp-abcdef-attempt-1"
    prompt = write_worker_prompt_bundle(
        request=req,
        role_spec=role_spec(),
        evidence_dir=evidence_root,
    )
    gateway = FakeGateway(
        ModelResponse(
            text="done",
            tool_calls=(),
            stop_reason="end",
            usage=Usage(
                cost_cny=0.2,
                input_tokens=10,
                output_tokens=3,
                cached_input_tokens=0,
            ),
        )
    )

    outcome = ModelGatewayRunner(gateway)(req)

    assert isinstance(outcome, WorkerCompleted)
    assert outcome.evidence == ("file:model/result.json",)
    assert outcome.spent_cny == 0.2
    assert gateway.opened == [("coder", "qwen-plus", 1.0)]
    assert gateway.session.requests[0].system == prompt.system
    assert gateway.session.requests[0].messages == (ModelMessage(role="user", content=prompt.user),)
    result = json.loads((evidence_root / "model" / "result.json").read_text(encoding="utf-8"))
    assert result["prompt_digest"] == prompt.digest
    assert result["stop_reason"] == "end"
    assert (evidence_root / "model" / "response.txt").read_text(encoding="utf-8") == "done"


def test_model_gateway_runner_fails_when_prompt_bundle_is_missing(git_repo: Path) -> None:
    outcome = ModelGatewayRunner(FakeGateway(success_response()))(request(git_repo))

    assert isinstance(outcome, WorkerFailed)
    assert outcome.reason_code == "runtime_error"
    result = json.loads(
        (
            git_repo / ".codentum" / "evidence" / "wp-abcdef-attempt-1" / "model" / "result.json"
        ).read_text(encoding="utf-8")
    )
    assert result["error"] == "prompt_bundle_error"


def test_model_gateway_runner_records_non_end_stop_reason_as_failure(git_repo: Path) -> None:
    req = request(git_repo)
    evidence_root = git_repo / ".codentum" / "evidence" / "wp-abcdef-attempt-1"
    write_worker_prompt_bundle(
        request=req,
        role_spec=role_spec(),
        evidence_dir=evidence_root,
    )
    response = ModelResponse(
        text="I cannot comply",
        tool_calls=(),
        stop_reason="refusal",
        usage=Usage(
            cost_cny=0.05,
            input_tokens=9,
            output_tokens=4,
            cached_input_tokens=0,
        ),
    )

    outcome = ModelGatewayRunner(FakeGateway(response))(req)

    assert isinstance(outcome, WorkerFailed)
    assert outcome.reason_code == "model_error"
    assert outcome.spent_cny == 0.05
    result = json.loads((evidence_root / "model" / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert result["stop_reason"] == "refusal"


def test_model_gateway_runner_treats_blocker_report_as_failure(git_repo: Path) -> None:
    req = request(git_repo)
    evidence_root = git_repo / ".codentum" / "evidence" / "wp-abcdef-attempt-1"
    write_worker_prompt_bundle(
        request=req,
        role_spec=role_spec(),
        evidence_dir=evidence_root,
    )
    response = ModelResponse(
        text=(
            "### Blocker Report\n\n"
            "The visible context does not provide any specific details about the task "
            "or the changes that need to be made."
        ),
        tool_calls=(),
        stop_reason="end",
        usage=Usage(
            cost_cny=0.07,
            input_tokens=13,
            output_tokens=11,
            cached_input_tokens=0,
        ),
    )

    outcome = ModelGatewayRunner(FakeGateway(response))(req)

    assert isinstance(outcome, WorkerFailed)
    assert outcome.reason_code == "acceptance_not_met"
    assert outcome.spent_cny == 0.07
    result = json.loads((evidence_root / "model" / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert result["error"] == "blocker_report"
    assert result["detail"] == "explicit blocker heading"


def test_model_gateway_runner_allows_non_blocker_completion(git_repo: Path) -> None:
    req = request(git_repo)
    evidence_root = git_repo / ".codentum" / "evidence" / "wp-abcdef-attempt-1"
    write_worker_prompt_bundle(
        request=req,
        role_spec=role_spec(),
        evidence_dir=evidence_root,
    )
    response = ModelResponse(
        text="Implemented blocker report handling in the runner.",
        tool_calls=(),
        stop_reason="end",
        usage=Usage(
            cost_cny=0.03,
            input_tokens=10,
            output_tokens=6,
            cached_input_tokens=0,
        ),
    )

    outcome = ModelGatewayRunner(FakeGateway(response))(req)

    assert isinstance(outcome, WorkerCompleted)
    result = json.loads((evidence_root / "model" / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "completed"
    assert "error" not in result


def success_response() -> ModelResponse:
    return ModelResponse(
        text="done",
        tool_calls=(),
        stop_reason="end",
        usage=Usage(
            cost_cny=0.01,
            input_tokens=1,
            output_tokens=1,
            cached_input_tokens=0,
        ),
    )


class FakeGateway:
    def __init__(self, response: ModelResponse) -> None:
        self.session = FakeSession(response)
        self.opened: list[tuple[RoleId, ModelId, float]] = []

    async def open(self, role: RoleId, routing: ModelRouting, grant_cny: float) -> FakeSession:
        self.opened.append((role, routing.model, grant_cny))
        return self.session

    async def estimate(self, routing: ModelRouting, req: ModelRequest) -> CostEstimate:
        return CostEstimate(estimated_cny=0.01, upper_bound_cny=0.02)

    async def ledger(self) -> CostLedger:
        return CostLedger(total_cny=0.0, by_role={}, by_model={}, since="2026-08-09T00:00:00Z")


class FakeSession:
    def __init__(self, response: ModelResponse) -> None:
        self.response = response
        self.requests: list[ModelRequest] = []
        self.closed = False

    @property
    def session_id(self) -> str:
        return "session-1"

    @property
    def model(self) -> ModelId:
        return "qwen-plus"

    @property
    def role(self) -> RoleId:
        return "coder"

    async def invoke(self, req: ModelRequest) -> ModelResponse:
        self.requests.append(req)
        return self.response

    def stream(self, req: ModelRequest) -> AsyncIterator[Any]:
        return _empty_stream(req)

    def spent_cny(self) -> float:
        return self.response.usage.cost_cny

    async def close(self) -> None:
        self.closed = True


async def _empty_stream(req: ModelRequest) -> AsyncIterator[Any]:
    items: tuple[Any, ...] = ()
    for item in items:
        yield (req, item)


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


def test_empty_tool_arguments_parse_as_no_arguments() -> None:
    """★ 空 arguments 是 OpenAI 格式里「这个工具没有参数」的合法表示。

    2026-08-12 实测：模型连写两次文件都成功，第三轮想调 list_files 确认
    文件在不在，`arguments` 发的是空串 —— 解析器一律 json.loads，
    于是把**一次本该成功的开发**打成了 model_error。

    ★ 只放行空串；真正畸形的 JSON 仍要抛错，那是应该抛的。
    """

    from codentum_harness.model_gateway.openai_compatible import _json_mapping_field

    assert _json_mapping_field({"arguments": ""}, "arguments") == {}
    assert _json_mapping_field({"arguments": "   "}, "arguments") == {}
    assert _json_mapping_field({"arguments": '{"path": "a.py"}'}, "arguments") == {"path": "a.py"}

    import pytest as _pytest

    with _pytest.raises(ValueError, match="JSON object text"):
        _json_mapping_field({"arguments": "{不是 JSON"}, "arguments")
