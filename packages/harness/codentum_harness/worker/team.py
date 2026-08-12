"""AgentTeams-backed WorkerRuntime adapter.

The first Team-mode slice provisions and observes an AgentTeams worker resource
through an injectable client. It deliberately fails closed at settle time until
Codentum task dispatch and result collection over AgentTeams are implemented.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
from collections.abc import AsyncIterator, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from shutil import which
from typing import Protocol

from codentum_contracts.interfaces import (
    AbortReason,
    CheckpointRef,
    FailureCode,
    SpawnRequest,
    WorkerAborted,
    WorkerEvent,
    WorkerFailed,
    WorkerHandle,
    WorkerOutcome,
)
from codentum_contracts.state import EvidenceRef, RoleId, RoleSpec
from codentum_roles import load_builtin_role_specs

from codentum_harness.checkpoint import write_initial_checkpoint
from codentum_harness.context_broker import (
    DEFAULT_INTENT_CONTEXT_CHAR_BUDGET,
    PACKET_INTENT_REF,
    ContextBundle,
    ContextCandidate,
    assemble_context_bundle,
    packet_intent_candidate,
)
from codentum_harness.prepare import PreparedExecution
from codentum_harness.prompt_bundle import WorkerPromptBundle, write_worker_prompt_bundle

from .local import WorkerContextLoader

__all__ = [
    "AgentTeamsCLIError",
    "AgentTeamsClient",
    "AgentTeamsDockerCLIClient",
    "AgentTeamsDockerCLIConfig",
    "AgentTeamsWorkerSpec",
    "AgentTeamsWorkerStatus",
    "TeamWorkerRuntime",
]


@dataclass(frozen=True, slots=True)
class AgentTeamsWorkerSpec:
    name: str
    model: str
    runtime: str
    identity: str
    wait_timeout_seconds: float


@dataclass(frozen=True, slots=True)
class AgentTeamsWorkerStatus:
    name: str
    phase: str
    model: str
    runtime: str
    container_state: str | None = None
    matrix_user_id: str | None = None
    room_id: str | None = None
    message: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, object]) -> AgentTeamsWorkerStatus:
        return cls(
            name=str(data.get("name", "")),
            phase=str(data.get("phase", "")),
            model=str(data.get("model", "")),
            runtime=str(data.get("runtime", "")),
            container_state=_optional_str(data.get("containerState")),
            matrix_user_id=_optional_str(data.get("matrixUserID")),
            room_id=_optional_str(data.get("roomID")),
            message=_optional_str(data.get("message")),
        )


class AgentTeamsClient(Protocol):
    def create_worker(self, spec: AgentTeamsWorkerSpec) -> None: ...

    def worker_status(self, name: str) -> AgentTeamsWorkerStatus: ...


@dataclass(frozen=True, slots=True)
class AgentTeamsDockerCLIConfig:
    controller_container: str = "agentteams-controller"
    docker_executable: str | None = None


class AgentTeamsCLIError(RuntimeError):
    pass


class AgentTeamsDockerCLIClient:
    """Minimal adapter around the official `agt` CLI inside the controller."""

    def __init__(self, config: AgentTeamsDockerCLIConfig | None = None) -> None:
        self._config = config or AgentTeamsDockerCLIConfig()

    def create_worker(self, spec: AgentTeamsWorkerSpec) -> None:
        result = self._run(
            (
                "create",
                "worker",
                "--name",
                spec.name,
                "--runtime",
                spec.runtime,
                "--model",
                spec.model,
                "--identity",
                spec.identity,
                "--wait-timeout",
                _duration_arg(spec.wait_timeout_seconds),
            ),
            check=False,
        )
        output = f"{result.stdout}\n{result.stderr}"
        if result.returncode != 0 and "did not become ready within" not in output:
            raise AgentTeamsCLIError(_summarize_process_failure(result))

    def worker_status(self, name: str) -> AgentTeamsWorkerStatus:
        result = self._run(("worker", "status", "--name", name, "-o", "json"))
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise AgentTeamsCLIError(f"invalid AgentTeams status JSON for worker {name}: {exc}") from exc
        if not isinstance(data, dict):
            raise AgentTeamsCLIError(f"invalid AgentTeams status payload for worker {name}")
        return AgentTeamsWorkerStatus.from_json(data)

    def _run(
        self,
        args: Sequence[str],
        *,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        docker = self._config.docker_executable or _docker()
        result = subprocess.run(  # noqa: S603 - fixed executable, no shell, args are structured.
            (
                docker,
                "exec",
                self._config.controller_container,
                "agt",
                *args,
            ),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
        )
        if check and result.returncode != 0:
            raise AgentTeamsCLIError(_summarize_process_failure(result))
        return result


class TeamWorkerRuntime:
    """AgentTeams implementation of the frozen WorkerRuntime protocol."""

    def __init__(
        self,
        *,
        repo_root: Path | str,
        client: AgentTeamsClient | None = None,
        role_specs: tuple[RoleSpec, ...] | None = None,
        context_loader: WorkerContextLoader | None = None,
        context_char_budget: int | None = None,
        worker_runtime: str = "copaw",
        worker_name_prefix: str = "codentum",
        create_wait_timeout_seconds: float = 300.0,
    ) -> None:
        if context_loader is not None and context_char_budget is None:
            raise ValueError("context_char_budget is required when context_loader is provided")
        if create_wait_timeout_seconds <= 0:
            raise ValueError("create_wait_timeout_seconds must be positive")

        self._repo_root = Path(repo_root)
        self._client = client or AgentTeamsDockerCLIClient()
        specs = load_builtin_role_specs() if role_specs is None else role_specs
        self._role_specs = {spec.id: spec for spec in specs}
        self._context_loader = context_loader
        self._context_char_budget = context_char_budget or DEFAULT_INTENT_CONTEXT_CHAR_BUDGET
        self._worker_runtime = worker_runtime
        self._worker_name_prefix = worker_name_prefix
        self._create_wait_timeout_seconds = create_wait_timeout_seconds
        self._sessions: dict[str, _TeamSession] = {}

    async def spawn(self, req: SpawnRequest) -> WorkerHandle:
        prepared = self._prepare(req)
        return await self._spawn(prepared)

    def _prepare(self, req: SpawnRequest) -> PreparedExecution:
        spec = self._load_role_spec(req.role)
        effective_req = req if req.tools else replace(req, tools=tuple(spec.tools))
        context_candidates = self._context_candidates(effective_req, spec)
        context = assemble_context_bundle(
            spec,
            candidates=context_candidates,
            char_budget=self._context_char_budget,
        )
        return PreparedExecution(
            request=effective_req,
            role_spec=spec,
            tools=tuple(effective_req.tools),
            mount_paths=tuple(m.mount_path for m in effective_req.mounts),
            context=context,
        )

    def _context_candidates(
        self,
        req: SpawnRequest,
        spec: RoleSpec,
    ) -> tuple[ContextCandidate, ...]:
        packet_intent = packet_intent_candidate(req, repo_root=self._repo_root)
        if self._context_loader is not None:
            loaded = self._context_loader(req, spec)
            if any(candidate.ref == PACKET_INTENT_REF for candidate in loaded):
                return tuple(loaded)
            return (packet_intent, *loaded)
        return (packet_intent,)

    async def _spawn(self, prepared: PreparedExecution) -> WorkerHandle:
        req = prepared.request
        worker_id = f"{req.packet_id}-attempt-{req.attempt}"
        if worker_id in self._sessions:
            raise RuntimeError(f"worker already exists: {worker_id}")

        workspace = Path(req.workspace)
        workspace.mkdir(parents=True, exist_ok=True)
        worker_name = _worker_name(self._worker_name_prefix, req.role, str(req.packet_id), req.attempt)
        handle = WorkerHandle(
            worker_id=worker_id,
            packet_id=req.packet_id,
            role=req.role,
            runtime_ref=f"agentteams://worker/{worker_name}",
        )
        evidence_dir = workspace / ".codentum" / "evidence" / worker_id
        session = _TeamSession(
            handle=handle,
            request=req,
            evidence_dir=evidence_dir,
            agentteams_worker=worker_name,
        )
        self._sessions[worker_id] = session
        session.write_manifest(workspace, worker_runtime=self._worker_runtime)
        checkpoint = session.write_checkpoint0(prepared.context)
        session.write_prompt_bundle(prepared.role_spec, prepared.context)
        session.append(
            "started",
            {
                "runtime_mode": "agentteams",
                "worker": worker_name,
                "workspace": str(workspace),
                "tools": list(req.tools),
                "mounts": [m.mount_path for m in req.mounts],
            },
        )
        session.append(
            "checkpoint",
            {
                "checkpoint_seq": checkpoint.seq,
                "digest": checkpoint.digest,
                "path": "checkpoints/0000.json",
            },
        )

        spec = AgentTeamsWorkerSpec(
            name=worker_name,
            model=str(req.routing.model),
            runtime=self._worker_runtime,
            identity=_identity(req.role, str(req.packet_id)),
            wait_timeout_seconds=self._create_wait_timeout_seconds,
        )
        try:
            await asyncio.to_thread(self._client.create_worker, spec)
            status = await asyncio.to_thread(self._client.worker_status, worker_name)
            session.write_status(status)
            session.append(
                "progress",
                _agentteams_status_event_payload(status, worker_name=worker_name),
            )
        except Exception as exc:
            session.write_error(exc)
            session.outcome = WorkerFailed(
                reason_code=FailureCode.RUNTIME_ERROR,
                detail=f"agentteams worker provisioning failed: {exc}",
                evidence=(EvidenceRef("file:agentteams/error.json"),),
                spent_cny=0.0,
            )
            session.append(
                "finished",
                _agentteams_error_event_payload(
                    exc,
                    worker_name=worker_name,
                    status=session.outcome.status,
                    reason="provisioning_failed",
                ),
            )
        return handle

    async def events(self, handle: WorkerHandle, since_seq: int = 0) -> AsyncIterator[WorkerEvent]:
        session = self._get(handle)
        for event in session.events:
            if event.seq > since_seq:
                yield event

    async def settle(self, handle: WorkerHandle) -> WorkerOutcome:
        session = self._get(handle)
        if session.outcome is not None:
            return session.outcome

        try:
            status = await asyncio.to_thread(self._client.worker_status, session.agentteams_worker)
            session.write_status(status)
            session.append(
                "progress",
                _agentteams_status_event_payload(status, worker_name=session.agentteams_worker),
            )
        except Exception as exc:
            session.write_error(exc)
            session.outcome = WorkerFailed(
                reason_code=FailureCode.RUNTIME_ERROR,
                detail=f"agentteams worker status check failed: {exc}",
                evidence=(EvidenceRef("file:agentteams/error.json"),),
                spent_cny=0.0,
            )
            session.append(
                "finished",
                _agentteams_error_event_payload(
                    exc,
                    worker_name=session.agentteams_worker,
                    status=session.outcome.status,
                    reason="status_failed",
                ),
            )
            return session.outcome

        evidence = (EvidenceRef("file:agentteams/status.json"),)
        if status.phase.lower() != "running":
            detail = f"agentteams worker is not running: phase={status.phase}"
            if status.message:
                detail = f"{detail}, message={status.message}"
        else:
            detail = (
                "agentteams worker is running, but Codentum task dispatch and "
                "result collection are not implemented yet"
            )
        session.outcome = WorkerFailed(
            reason_code=FailureCode.RUNTIME_ERROR,
            detail=detail,
            evidence=evidence,
            spent_cny=0.0,
        )
        session.append(
            "finished",
            {
                **_agentteams_status_event_payload(status, worker_name=session.agentteams_worker),
                "status": session.outcome.status,
                "reason": "team_dispatch_missing",
                "moduleState": "failed",
            },
        )
        return session.outcome

    async def abort(self, handle: WorkerHandle, reason: AbortReason) -> None:
        session = self._get(handle)
        session.outcome = WorkerAborted(reason=reason, spent_cny=0.0)
        session.append("finished", {"status": session.outcome.status, "reason": reason})

    async def resume(self, ref: CheckpointRef) -> WorkerHandle:
        raise NotImplementedError(f"checkpoint resume is not implemented yet: {ref.worker_id}")

    async def adopt(self, runtime_ref: str) -> WorkerHandle | None:
        for session in self._sessions.values():
            if session.handle.runtime_ref == runtime_ref:
                return session.handle
        return None

    def _load_role_spec(self, role: RoleId) -> RoleSpec:
        spec = self._role_specs.get(role)
        if spec is None:
            raise RuntimeError(f"RoleSpec is not loaded for role: {role}")
        return spec

    def _get(self, handle: WorkerHandle) -> _TeamSession:
        session = self._sessions.get(handle.worker_id)
        if session is None:
            raise KeyError(f"unknown worker: {handle.worker_id}")
        return session


class _TeamSession:
    def __init__(
        self,
        *,
        handle: WorkerHandle,
        request: SpawnRequest,
        evidence_dir: Path,
        agentteams_worker: str,
    ) -> None:
        self.handle = handle
        self.request = request
        self.evidence_dir = evidence_dir
        self.agentteams_worker = agentteams_worker
        self.events_file = evidence_dir / "events.jsonl"
        self.events: list[WorkerEvent] = []
        self.outcome: WorkerOutcome | None = None

    def write_manifest(self, workspace: Path, *, worker_runtime: str) -> None:
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "worker_id": self.handle.worker_id,
            "packet_id": self.handle.packet_id,
            "role": self.handle.role,
            "attempt": self.request.attempt,
            "workspace": str(workspace),
            "tools": list(self.request.tools),
            "mounts": [asdict(m) for m in self.request.mounts],
            "created_at": datetime.now(UTC).isoformat(),
            "runtime_mode": "agentteams",
            "agentteams": {
                "worker": self.agentteams_worker,
                "runtime": worker_runtime,
                "model": self.request.routing.model,
            },
        }
        (self.evidence_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

    def write_checkpoint0(self, context: ContextBundle | None) -> CheckpointRef:
        return write_initial_checkpoint(
            worker_id=self.handle.worker_id,
            request=self.request,
            evidence_dir=self.evidence_dir,
            context=context,
        )

    def write_prompt_bundle(
        self,
        role_spec: RoleSpec,
        context: ContextBundle | None,
    ) -> WorkerPromptBundle:
        return write_worker_prompt_bundle(
            request=self.request,
            role_spec=role_spec,
            evidence_dir=self.evidence_dir,
            context=context,
        )

    def write_status(self, status: AgentTeamsWorkerStatus) -> None:
        agentteams_dir = self.evidence_dir / "agentteams"
        agentteams_dir.mkdir(parents=True, exist_ok=True)
        (agentteams_dir / "status.json").write_text(
            json.dumps(asdict(status), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

    def write_error(self, exc: Exception) -> None:
        agentteams_dir = self.evidence_dir / "agentteams"
        agentteams_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "error_type": type(exc).__name__,
            "detail": str(exc),
        }
        (agentteams_dir / "error.json").write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

    def append(self, kind: str, payload: dict[str, object]) -> None:
        event = WorkerEvent(
            kind=kind,  # type: ignore[arg-type]
            at=datetime.now(UTC).isoformat(),
            seq=len(self.events) + 1,
            payload=payload,
        )
        self.events.append(event)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        with self.events_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(event), ensure_ascii=False, sort_keys=True) + "\n")


def _worker_name(prefix: str, role: RoleId, packet_id: str, attempt: int) -> str:
    raw = f"{prefix}-{role}-{packet_id}-a{attempt}".lower()
    normalized = re.sub(r"[^a-z0-9-]+", "-", raw)
    normalized = re.sub(r"-+", "-", normalized).strip("-")
    if not normalized:
        raise ValueError("agentteams worker name is empty after normalization")
    return normalized[:63].rstrip("-")


def _identity(role: RoleId, packet_id: str) -> str:
    return f"Codentum {role} worker for packet {packet_id}."


def _agentteams_status_event_payload(
    status: AgentTeamsWorkerStatus,
    *,
    worker_name: str,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "runtime_mode": "agentteams",
        "moduleId": "agentteams.worker",
        "moduleLabel": "AgentTeams Worker",
        "moduleState": _agentteams_module_state(status.phase),
        "agentteams_worker": worker_name,
        "phase": status.phase,
        "model": status.model,
        "runtime": status.runtime,
        "status_ref": "file:agentteams/status.json",
    }
    optional = {
        "container_state": status.container_state,
        "matrix_user_id": status.matrix_user_id,
        "room_id": status.room_id,
        "message": status.message,
    }
    payload.update({key: value for key, value in optional.items() if value is not None})
    return payload


def _agentteams_error_event_payload(
    exc: Exception,
    *,
    worker_name: str,
    status: str,
    reason: str,
) -> dict[str, object]:
    return {
        "runtime_mode": "agentteams",
        "moduleId": "agentteams.worker",
        "moduleLabel": "AgentTeams Worker",
        "moduleState": "failed",
        "agentteams_worker": worker_name,
        "status": status,
        "reason": reason,
        "error_type": type(exc).__name__,
        "error_detail": str(exc),
        "error_ref": "file:agentteams/error.json",
    }


def _agentteams_module_state(phase: str) -> str:
    normalized = phase.lower()
    if normalized in {"running", "ready"}:
        return "running"
    if normalized in {"failed", "error"}:
        return "failed"
    if normalized in {"pending", "creating", "initializing", "starting"}:
        return "waiting"
    return "unknown"


def _duration_arg(seconds: float) -> str:
    rounded = max(1, int(seconds))
    return f"{rounded}s"


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _docker() -> str:
    exe = which("docker")
    if exe is None:
        raise AgentTeamsCLIError("docker executable not found")
    return exe


def _summarize_process_failure(result: subprocess.CompletedProcess[str]) -> str:
    output = (result.stderr or result.stdout).strip()
    if output:
        return output
    return f"command exited with status {result.returncode}"
