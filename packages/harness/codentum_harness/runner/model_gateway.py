"""Run one model invocation through the frozen ModelGateway contract."""

from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from shutil import which
from typing import Any

from codentum_contracts import EvidenceRef
from codentum_contracts.interfaces import (
    FailureCode,
    ModelGateway,
    ModelResponse,
    ModelSession,
    SpawnRequest,
    WorkerCompleted,
    WorkerFailed,
    WorkerOutcome,
)

from codentum_harness.prompt_bundle import PromptBundleError, WorkerPromptBundle, load_worker_prompt_bundle

__all__ = [
    "ModelGatewayRunner",
]


@dataclass(frozen=True, slots=True)
class ModelGatewayRunner:
    """WorkerRunner adapter for a single ModelGateway invocation."""

    gateway: ModelGateway
    timeout_seconds: float = 120.0

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

    def __call__(self, req: SpawnRequest) -> WorkerOutcome:
        paths = _RunnerPaths.from_request(req)
        try:
            return asyncio.run(asyncio.wait_for(self._run(req, paths), timeout=self.timeout_seconds))
        except TimeoutError:
            evidence = _write_result(
                paths.model_dir,
                {
                    "status": "timeout",
                    "timeout_seconds": self.timeout_seconds,
                },
            )
            return WorkerFailed(
                reason_code=FailureCode.TIMEOUT,
                detail=f"model runner timed out after {self.timeout_seconds:g}s",
                evidence=(evidence,),
                spent_cny=0.0,
            )
        except Exception as exc:
            evidence = _write_result(
                paths.model_dir,
                {
                    "status": "failed",
                    "error": "runtime_error",
                    "detail": str(exc),
                },
            )
            return WorkerFailed(
                reason_code=FailureCode.RUNTIME_ERROR,
                detail=str(exc),
                evidence=(evidence,),
                spent_cny=0.0,
            )

    async def _run(self, req: SpawnRequest, paths: _RunnerPaths) -> WorkerOutcome:
        try:
            prompt = load_worker_prompt_bundle(paths.evidence_root)
        except PromptBundleError as exc:
            evidence = _write_result(
                paths.model_dir,
                {
                    "status": "failed",
                    "error": "prompt_bundle_error",
                    "detail": str(exc),
                },
            )
            return WorkerFailed(
                reason_code=FailureCode.RUNTIME_ERROR,
                detail=str(exc),
                evidence=(evidence,),
                spent_cny=0.0,
            )

        session: ModelSession | None = None
        try:
            session = await self.gateway.open(req.role, req.routing, req.budget.limit_cny)
            response = await session.invoke(prompt.to_model_request(effort=req.routing.effort))
        except Exception as exc:
            evidence = _write_result(
                paths.model_dir,
                {
                    "status": "failed",
                    "error": "model_gateway_error",
                    "detail": str(exc),
                    "prompt_digest": prompt.digest,
                    "prompt_manifest_path": "prompt/manifest.json",
                },
            )
            return WorkerFailed(
                reason_code=FailureCode.MODEL_ERROR,
                detail=str(exc),
                evidence=(evidence,),
                spent_cny=0.0,
            )
        finally:
            if session is not None:
                await session.close()

        return _record_response(req, paths=paths, prompt=prompt, session=session, response=response)


@dataclass(frozen=True, slots=True)
class _RunnerPaths:
    workspace: Path
    evidence_root: Path
    model_dir: Path

    @classmethod
    def from_request(cls, req: SpawnRequest) -> _RunnerPaths:
        workspace = Path(req.workspace)
        worker_id = f"{req.packet_id}-attempt-{req.attempt}"
        evidence_root = workspace / ".codentum" / "evidence" / worker_id
        return cls(
            workspace=workspace,
            evidence_root=evidence_root,
            model_dir=evidence_root / "model",
        )


def _record_response(
    req: SpawnRequest,
    *,
    paths: _RunnerPaths,
    prompt: WorkerPromptBundle,
    session: ModelSession,
    response: ModelResponse,
) -> WorkerOutcome:
    paths.model_dir.mkdir(parents=True, exist_ok=True)
    (paths.model_dir / "response.txt").write_text(response.text, encoding="utf-8")
    (paths.model_dir / "usage.json").write_text(
        json.dumps(asdict(response.usage), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    (paths.model_dir / "tool_calls.json").write_text(
        json.dumps(_tool_calls(response), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    spent_cny = max(response.usage.cost_cny, _session_spent_cny(session))
    status = "completed" if response.stop_reason == "end" and not response.tool_calls else "failed"
    result: dict[str, object] = {
        "status": status,
        "model": session.model,
        "session_id": session.session_id,
        "role": session.role,
        "stop_reason": response.stop_reason,
        "spent_cny": spent_cny,
        "prompt_digest": prompt.digest,
        "prompt_manifest_path": "prompt/manifest.json",
        "response_path": "response.txt",
        "tool_calls_path": "tool_calls.json",
        "usage_path": "usage.json",
    }
    evidence = _write_result(paths.model_dir, result)

    if status != "completed":
        detail = (
            "model requested tool calls but ModelGatewayRunner has no tool loop yet"
            if response.tool_calls
            else f"model stopped with reason: {response.stop_reason}"
        )
        return WorkerFailed(
            reason_code=FailureCode.MODEL_ERROR,
            detail=detail,
            evidence=(evidence,),
            spent_cny=spent_cny,
        )

    return WorkerCompleted(
        evidence=(evidence,),
        spent_cny=spent_cny,
        touched_paths=_git_changed_paths(paths.workspace),
    )


def _tool_calls(response: ModelResponse) -> list[dict[str, Any]]:
    return [asdict(tool_call) for tool_call in response.tool_calls]


def _write_result(model_dir: Path, result: dict[str, object]) -> EvidenceRef:
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    worker_evidence = model_dir.parent
    result_path = (model_dir / "result.json").relative_to(worker_evidence).as_posix()
    return EvidenceRef(f"file:{result_path}")


def _session_spent_cny(session: ModelSession) -> float:
    try:
        return session.spent_cny()
    except Exception:
        return 0.0


def _git_changed_paths(workspace: Path) -> tuple[str, ...]:
    git = which("git")
    if git is None:
        return ()

    try:
        out = subprocess.run(  # noqa: S603 - fixed git invocation, shell=False.
            [git, "-C", str(workspace), "status", "--porcelain", "--untracked-files=all"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout
    except (FileNotFoundError, subprocess.CalledProcessError):
        return ()

    paths: list[str] = []
    for line in out.splitlines():
        if len(line) >= 4:
            paths.append(line[3:])
    return tuple(sorted(paths))
