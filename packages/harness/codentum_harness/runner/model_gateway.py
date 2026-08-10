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

_BLOCKER_HEADING_PREFIXES = (
    "blocker report",
    "blocker:",
    "blocked:",
    "blocking issue",
    "cannot proceed",
    "can't proceed",
    "unable to proceed",
    "阻塞报告",
    "无法继续",
    "不能继续",
)


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
    blocker_reason = _explicit_blocker_reason(response.text)
    status = (
        "completed"
        if blocker_reason is None and response.stop_reason == "end" and not response.tool_calls
        else "failed"
    )
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
    if blocker_reason is not None:
        result["error"] = "blocker_report"
        result["detail"] = blocker_reason
    evidence = _write_result(paths.model_dir, result)

    if blocker_reason is not None:
        return WorkerFailed(
            reason_code=FailureCode.ACCEPTANCE_NOT_MET,
            detail=f"model reported blocker: {blocker_reason}",
            evidence=(evidence,),
            spent_cny=spent_cny,
        )

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


def _explicit_blocker_reason(text: str) -> str | None:
    """Classify explicit self-reported blockers without guessing task semantics."""

    normalized = _normalized_text(text)
    if not normalized:
        return None

    for line in _first_nonempty_lines(text, limit=10):
        if line.startswith(_BLOCKER_HEADING_PREFIXES):
            return "explicit blocker heading"

    if "visible context" in normalized and "does not provide" in normalized:
        return "visible context does not provide task details"
    if "visible context" in normalized and "no task" in normalized:
        return "visible context missing task details"
    if "insufficient context" in normalized and ("task" in normalized or "changes" in normalized):
        return "insufficient context for requested task"
    if "可见上下文" in normalized and ("没有" in normalized or "不足" in normalized):
        return "visible context missing task details"
    return None


def _first_nonempty_lines(text: str, *, limit: int) -> list[str]:
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = _strip_markdown_prefix(raw_line).lower()
        if not line:
            continue
        lines.append(line)
        if len(lines) == limit:
            break
    return lines


def _strip_markdown_prefix(line: str) -> str:
    return line.strip().lstrip("#>*_-0123456789. \t").strip()


def _normalized_text(text: str) -> str:
    return " ".join(text.lower().split())


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
