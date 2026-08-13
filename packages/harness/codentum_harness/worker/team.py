"""AgentTeams-backed WorkerRuntime adapter.

Team mode keeps the frozen Codentum ``WorkerRuntime`` contract intact while the
harness privately prepares an AgentTeams task, dispatches it to the configured
AgentTeams client, and collects a terminal result with auditable evidence.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from shutil import which
from typing import Literal, Protocol
from urllib.parse import quote

from codentum_contracts.interfaces import (
    AbortReason,
    CheckpointRef,
    FailureCode,
    SpawnRequest,
    WorkerAborted,
    WorkerCompleted,
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
    "AgentTeamsDispatchReceipt",
    "AgentTeamsDockerCLIClient",
    "AgentTeamsDockerCLIConfig",
    "AgentTeamsTaskResult",
    "AgentTeamsTaskSpec",
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


@dataclass(frozen=True, slots=True)
class AgentTeamsTaskSpec:
    task_id: str
    worker_name: str
    packet_id: str
    role: RoleId
    attempt: int
    workspace: str
    model: str
    budget_cny: float
    tools: tuple[str, ...]
    context_refs: tuple[str, ...]
    prompt_digest: str
    prompt_ref: EvidenceRef
    system_prompt: str
    user_prompt: str


@dataclass(frozen=True, slots=True)
class AgentTeamsDispatchReceipt:
    task_id: str
    worker_name: str
    transport: str
    target: str
    external_id: str | None
    submitted_at: str
    detail: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AgentTeamsTaskResult:
    status: Literal["completed", "failed"]
    detail: str
    evidence: tuple[EvidenceRef, ...] = ()
    spent_cny: float = 0.0
    touched_paths: tuple[str, ...] = ()
    reason_code: FailureCode = FailureCode.RUNTIME_ERROR
    metadata: Mapping[str, object] = field(default_factory=dict)


class AgentTeamsClient(Protocol):
    def create_worker(self, spec: AgentTeamsWorkerSpec) -> None: ...

    def worker_status(self, name: str) -> AgentTeamsWorkerStatus: ...

    def dispatch_task(self, spec: AgentTeamsTaskSpec) -> AgentTeamsDispatchReceipt: ...

    def collect_result(
        self,
        spec: AgentTeamsTaskSpec,
        receipt: AgentTeamsDispatchReceipt,
    ) -> AgentTeamsTaskResult: ...


@dataclass(frozen=True, slots=True)
class AgentTeamsDockerCLIConfig:
    controller_container: str = "agentteams-controller"
    manager_container: str = "agentteams-manager"
    docker_executable: str | None = None
    env_file: str | None = None
    matrix_url: str | None = None
    matrix_domain: str | None = None
    admin_user: str | None = None
    admin_password: str | None = None
    result_wait_timeout_seconds: float = 300.0
    result_poll_seconds: float = 5.0


class AgentTeamsCLIError(RuntimeError):
    pass


class AgentTeamsDockerCLIClient:
    """Adapter around AgentTeams resource CLI plus Matrix Manager dispatch."""

    def __init__(self, config: AgentTeamsDockerCLIConfig | None = None) -> None:
        self._config = config or AgentTeamsDockerCLIConfig()
        if self._config.result_wait_timeout_seconds <= 0:
            raise ValueError("result_wait_timeout_seconds must be positive")
        if self._config.result_poll_seconds <= 0:
            raise ValueError("result_poll_seconds must be positive")

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

    def dispatch_task(self, spec: AgentTeamsTaskSpec) -> AgentTeamsDispatchReceipt:
        matrix = self._matrix_config()
        token = self._matrix_login(matrix)
        room_id = self._find_manager_room(matrix, token)
        if room_id is None:
            room_id = self._create_manager_room(matrix, token)

        submitted_at = datetime.now(UTC).isoformat()
        message = _agentteams_task_message(spec)
        event_id = self._matrix_send_message(
            matrix,
            token=token,
            room_id=room_id,
            body=message,
            txn_id=_matrix_txn_id(spec.task_id, submitted_at),
        )
        return AgentTeamsDispatchReceipt(
            task_id=spec.task_id,
            worker_name=spec.worker_name,
            transport="matrix",
            target=room_id,
            external_id=event_id,
            submitted_at=submitted_at,
            detail="sent Codentum task prompt to AgentTeams Manager room",
            metadata={"manager_user": f"@manager:{matrix.domain}"},
        )

    def collect_result(
        self,
        spec: AgentTeamsTaskSpec,
        receipt: AgentTeamsDispatchReceipt,
    ) -> AgentTeamsTaskResult:
        matrix = self._matrix_config()
        token = self._matrix_login(matrix)
        deadline = time.monotonic() + self._config.result_wait_timeout_seconds
        last_error = ""

        while time.monotonic() <= deadline:
            try:
                payload = self._matrix_read_messages(matrix, token=token, room_id=receipt.target, limit=50)
            except AgentTeamsCLIError as exc:
                last_error = str(exc)
            else:
                result = _extract_codentum_result(payload, spec.task_id)
                if result is not None:
                    return result
            time.sleep(self._config.result_poll_seconds)

        detail = (
            f"agentteams result marker for {spec.task_id} was not observed within "
            f"{self._config.result_wait_timeout_seconds:g}s"
        )
        if last_error:
            detail = f"{detail}; last Matrix read error: {last_error}"
        return AgentTeamsTaskResult(
            status="failed",
            detail=detail,
            reason_code=FailureCode.TIMEOUT,
            metadata={"dispatch_event_id": receipt.external_id or ""},
        )

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

    def _matrix_config(self) -> _MatrixConfig:
        env = _agentteams_env(self._config.env_file)
        admin_user = self._config.admin_user or env.get("AGENTTEAMS_ADMIN_USER") or "admin"
        admin_password = self._config.admin_password or env.get("AGENTTEAMS_ADMIN_PASSWORD") or ""
        if not admin_password:
            raise AgentTeamsCLIError(
                "AGENTTEAMS_ADMIN_PASSWORD is required for Matrix task dispatch; "
                "set it in env or ~/agentteams-manager.env"
            )
        matrix_domain = (
            self._config.matrix_domain
            or env.get("AGENTTEAMS_MATRIX_DOMAIN")
            or f"matrix-local.agentteams.io:{env.get('AGENTTEAMS_PORT_GATEWAY', '8080')}"
        )
        return _MatrixConfig(
            url=self._config.matrix_url or "http://127.0.0.1:6167",
            domain=matrix_domain,
            admin_user=admin_user,
            admin_password=admin_password,
        )

    def _matrix_login(self, config: _MatrixConfig) -> str:
        payload = {
            "type": "m.login.password",
            "identifier": {"type": "m.id.user", "user": config.admin_user},
            "password": config.admin_password,
        }
        data = self._matrix_json(config, "POST", "/_matrix/client/v3/login", payload)
        token = data.get("access_token")
        if not isinstance(token, str) or not token:
            raise AgentTeamsCLIError("Matrix login did not return access_token")
        return token

    def _find_manager_room(self, config: _MatrixConfig, token: str) -> str | None:
        joined = self._matrix_json(config, "GET", "/_matrix/client/v3/joined_rooms", token=token)
        room_ids = joined.get("joined_rooms")
        if not isinstance(room_ids, list):
            raise AgentTeamsCLIError("Matrix joined_rooms response is invalid")
        manager_user = f"@manager:{config.domain}"
        admin_user = f"@{config.admin_user}:{config.domain}"
        for item in room_ids:
            if not isinstance(item, str) or not item:
                continue
            members = self._matrix_json(
                config,
                "GET",
                f"/_matrix/client/v3/rooms/{_quote_room_id(item)}/members",
                token=token,
            )
            raw_chunk = members.get("chunk")
            if not isinstance(raw_chunk, list):
                continue
            users = {
                state_key
                for entry in raw_chunk
                if isinstance(entry, dict)
                if isinstance((state_key := entry.get("state_key")), str)
            }
            if manager_user in users and admin_user in users and len(users) == 2:
                return item
        return None

    def _create_manager_room(self, config: _MatrixConfig, token: str) -> str:
        manager_user = f"@manager:{config.domain}"
        data = self._matrix_json(
            config,
            "POST",
            "/_matrix/client/v3/createRoom",
            {
                "is_direct": True,
                "invite": [manager_user],
                "preset": "trusted_private_chat",
            },
            token=token,
        )
        room_id = data.get("room_id")
        if not isinstance(room_id, str) or not room_id:
            raise AgentTeamsCLIError("Matrix createRoom did not return room_id")
        return room_id

    def _matrix_send_message(
        self,
        config: _MatrixConfig,
        *,
        token: str,
        room_id: str,
        body: str,
        txn_id: str,
    ) -> str:
        data = self._matrix_json(
            config,
            "PUT",
            f"/_matrix/client/v3/rooms/{_quote_room_id(room_id)}/send/m.room.message/{txn_id}",
            {
                "msgtype": "m.text",
                "body": body,
            },
            token=token,
        )
        event_id = data.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            raise AgentTeamsCLIError("Matrix send did not return event_id")
        return event_id

    def _matrix_read_messages(
        self,
        config: _MatrixConfig,
        *,
        token: str,
        room_id: str,
        limit: int,
    ) -> dict[str, object]:
        return self._matrix_json(
            config,
            "GET",
            f"/_matrix/client/v3/rooms/{_quote_room_id(room_id)}/messages?dir=b&limit={limit}",
            token=token,
        )

    def _matrix_json(
        self,
        config: _MatrixConfig,
        method: str,
        path: str,
        payload: Mapping[str, object] | None = None,
        *,
        token: str | None = None,
    ) -> dict[str, object]:
        docker = self._config.docker_executable or _docker()
        command = [
            docker,
            "exec",
            self._config.manager_container,
            "curl",
            "-sf",
            "-X",
            method,
        ]
        if token is not None:
            command.extend(["-H", f"Authorization: Bearer {token}"])
        if payload is not None:
            command.extend(
                [
                    "-H",
                    "Content-Type: application/json",
                    "-d",
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                ]
            )
        command.append(f"{config.url}{path}")
        result = subprocess.run(  # noqa: S603 - fixed executable, no shell, args are structured.
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            raise AgentTeamsCLIError(_summarize_process_failure(result))
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise AgentTeamsCLIError(f"Matrix API response is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise AgentTeamsCLIError("Matrix API response must be a JSON object")
        return data


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
        prompt_bundle = session.write_prompt_bundle(
            prepared.role_spec,
            prepared.context,
            skills_dir=self._shared_skills_dir(),
        )
        session.task_spec = _agentteams_task_spec(
            req,
            worker_name=worker_name,
            prompt_bundle=prompt_bundle,
            context=prepared.context,
        )
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

        if status.phase.lower() != "running":
            detail = f"agentteams worker is not running: phase={status.phase}"
            if status.message:
                detail = f"{detail}, message={status.message}"
            session.outcome = WorkerFailed(
                reason_code=FailureCode.RUNTIME_ERROR,
                detail=detail,
                evidence=(EvidenceRef("file:agentteams/status.json"),),
                spent_cny=0.0,
            )
            session.append(
                "finished",
                {
                    **_agentteams_status_event_payload(status, worker_name=session.agentteams_worker),
                    "status": session.outcome.status,
                    "reason": "worker_not_running",
                    "moduleState": "failed",
                },
            )
            return session.outcome

        try:
            if session.dispatch_receipt is None:
                if session.task_spec is None:
                    raise AgentTeamsCLIError("agentteams task spec was not prepared")
                receipt = await asyncio.to_thread(self._client.dispatch_task, session.task_spec)
                session.dispatch_receipt = receipt
                session.write_dispatch(receipt)
                session.append("progress", _agentteams_dispatch_event_payload(receipt))
            if session.task_spec is None or session.dispatch_receipt is None:
                raise AgentTeamsCLIError("agentteams dispatch state is incomplete")
            result = await asyncio.to_thread(
                self._client.collect_result,
                session.task_spec,
                session.dispatch_receipt,
            )
            session.write_result(result)
            session.append("progress", _agentteams_result_event_payload(result))
        except Exception as exc:
            session.write_error(exc)
            session.outcome = WorkerFailed(
                reason_code=FailureCode.RUNTIME_ERROR,
                detail=f"agentteams task dispatch or result collection failed: {exc}",
                evidence=_agentteams_failure_evidence(session),
                spent_cny=0.0,
            )
            session.append(
                "finished",
                _agentteams_error_event_payload(
                    exc,
                    worker_name=session.agentteams_worker,
                    status=session.outcome.status,
                    reason="dispatch_or_collect_failed",
                ),
            )
            return session.outcome

        result_evidence = _agentteams_result_evidence(result)
        if result.status == "completed":
            session.outcome = WorkerCompleted(
                evidence=result_evidence,
                spent_cny=result.spent_cny,
                touched_paths=result.touched_paths,
            )
            session.append(
                "finished",
                _agentteams_finished_event_payload(
                    session.agentteams_worker,
                    status=session.outcome.status,
                    reason="team_result_collected",
                    detail=result.detail,
                ),
            )
            return session.outcome

        session.outcome = WorkerFailed(
            reason_code=result.reason_code,
            detail=result.detail,
            evidence=result_evidence,
            spent_cny=result.spent_cny,
        )
        session.append(
            "finished",
            _agentteams_finished_event_payload(
                session.agentteams_worker,
                status=session.outcome.status,
                reason="team_result_failed",
                detail=result.detail,
            ),
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

    def _shared_skills_dir(self) -> Path | None:
        skills_dir = self._repo_root / ".codentum" / "skills" / "shared"
        return skills_dir if skills_dir.is_dir() else None

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
        self.task_spec: AgentTeamsTaskSpec | None = None
        self.dispatch_receipt: AgentTeamsDispatchReceipt | None = None
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
        *,
        skills_dir: Path | str | None = None,
    ) -> WorkerPromptBundle:
        return write_worker_prompt_bundle(
            request=self.request,
            role_spec=role_spec,
            evidence_dir=self.evidence_dir,
            context=context,
            skills_dir=skills_dir,
        )

    def write_status(self, status: AgentTeamsWorkerStatus) -> None:
        agentteams_dir = self.evidence_dir / "agentteams"
        agentteams_dir.mkdir(parents=True, exist_ok=True)
        (agentteams_dir / "status.json").write_text(
            json.dumps(asdict(status), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

    def write_dispatch(self, receipt: AgentTeamsDispatchReceipt) -> None:
        agentteams_dir = self.evidence_dir / "agentteams"
        agentteams_dir.mkdir(parents=True, exist_ok=True)
        (agentteams_dir / "dispatch.json").write_text(
            json.dumps(asdict(receipt), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

    def write_result(self, result: AgentTeamsTaskResult) -> None:
        agentteams_dir = self.evidence_dir / "agentteams"
        agentteams_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            **asdict(result),
            "evidence": list(result.evidence),
            "reason_code": str(result.reason_code),
        }
        (agentteams_dir / "result.json").write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
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


def _agentteams_task_spec(
    req: SpawnRequest,
    *,
    worker_name: str,
    prompt_bundle: WorkerPromptBundle,
    context: ContextBundle | None,
) -> AgentTeamsTaskSpec:
    return AgentTeamsTaskSpec(
        task_id=_task_id(req),
        worker_name=worker_name,
        packet_id=str(req.packet_id),
        role=req.role,
        attempt=req.attempt,
        workspace=req.workspace,
        model=str(req.routing.model),
        budget_cny=req.budget.limit_cny,
        tools=tuple(req.tools),
        context_refs=tuple(context.refs) if context is not None else (),
        prompt_digest=prompt_bundle.digest,
        prompt_ref=EvidenceRef("file:prompt/manifest.json"),
        system_prompt=prompt_bundle.system,
        user_prompt=prompt_bundle.user,
    )


def _task_id(req: SpawnRequest) -> str:
    return f"{req.packet_id}-attempt-{req.attempt}"


def _agentteams_task_message(spec: AgentTeamsTaskSpec) -> str:
    control = {
        "schemaVersion": 1,
        "taskId": spec.task_id,
        "packetId": spec.packet_id,
        "role": spec.role,
        "attempt": spec.attempt,
        "workerName": spec.worker_name,
        "workspace": spec.workspace,
        "model": spec.model,
        "budgetCny": spec.budget_cny,
        "tools": list(spec.tools),
        "contextRefs": list(spec.context_refs),
        "promptDigest": spec.prompt_digest,
        "promptRef": spec.prompt_ref,
    }
    return "\n".join(
        [
            f"Please assign AgentTeams Worker `{spec.worker_name}` this Codentum task.",
            "",
            "The task is bounded. The final response must include exactly one line:",
            "",
            "CODENTUM_RESULT {\"taskId\":\""
            + spec.task_id
            + "\",\"status\":\"completed|failed\",\"detail\":\"...\","
            + "\"spentCny\":0,\"touchedPaths\":[],\"evidence\":[]}",
            "",
            "Do not mark the task completed until the worker has finished and produced evidence.",
            "",
            "```json",
            json.dumps(control, ensure_ascii=False, sort_keys=True, indent=2),
            "```",
            "",
            "## System Prompt",
            "",
            spec.system_prompt,
            "",
            "## User Prompt",
            "",
            spec.user_prompt,
        ]
    )


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


def _agentteams_dispatch_event_payload(receipt: AgentTeamsDispatchReceipt) -> dict[str, object]:
    payload: dict[str, object] = {
        "runtime_mode": "agentteams",
        "moduleId": "agentteams.dispatch",
        "moduleLabel": "AgentTeams Dispatch",
        "moduleState": "running",
        "agentteams_worker": receipt.worker_name,
        "task_id": receipt.task_id,
        "transport": receipt.transport,
        "target": receipt.target,
        "dispatch_ref": "file:agentteams/dispatch.json",
        "submitted_at": receipt.submitted_at,
    }
    if receipt.external_id is not None:
        payload["external_id"] = receipt.external_id
    if receipt.detail is not None:
        payload["detail"] = receipt.detail
    return payload


def _agentteams_result_event_payload(result: AgentTeamsTaskResult) -> dict[str, object]:
    return {
        "runtime_mode": "agentteams",
        "moduleId": "agentteams.result",
        "moduleLabel": "AgentTeams Result",
        "moduleState": "completed" if result.status == "completed" else "failed",
        "result_status": result.status,
        "detail": result.detail,
        "spent_cny": result.spent_cny,
        "touched_paths": list(result.touched_paths),
        "result_ref": "file:agentteams/result.json",
        "evidence": list(result.evidence),
    }


def _agentteams_finished_event_payload(
    worker_name: str,
    *,
    status: str,
    reason: str,
    detail: str,
) -> dict[str, object]:
    return {
        "runtime_mode": "agentteams",
        "moduleId": "agentteams.result",
        "moduleLabel": "AgentTeams Result",
        "moduleState": "completed" if status == "completed" else "failed",
        "agentteams_worker": worker_name,
        "status": status,
        "reason": reason,
        "detail": detail,
        "result_ref": "file:agentteams/result.json",
    }


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


def _agentteams_failure_evidence(session: _TeamSession) -> tuple[EvidenceRef, ...]:
    evidence = [EvidenceRef("file:agentteams/error.json")]
    if session.dispatch_receipt is not None:
        evidence.append(EvidenceRef("file:agentteams/dispatch.json"))
    return tuple(evidence)


def _agentteams_result_evidence(result: AgentTeamsTaskResult) -> tuple[EvidenceRef, ...]:
    evidence = [EvidenceRef("file:agentteams/status.json")]
    evidence.append(EvidenceRef("file:agentteams/dispatch.json"))
    evidence.append(EvidenceRef("file:agentteams/result.json"))
    evidence.extend(result.evidence)
    return tuple(dict.fromkeys(evidence))


def _extract_codentum_result(payload: Mapping[str, object], task_id: str) -> AgentTeamsTaskResult | None:
    chunk = payload.get("chunk")
    if not isinstance(chunk, list):
        return None
    for event in chunk:
        if not isinstance(event, dict):
            continue
        content = event.get("content")
        if not isinstance(content, dict):
            continue
        body = content.get("body")
        if not isinstance(body, str):
            continue
        parsed = _parse_codentum_result(body, task_id)
        if parsed is not None:
            return parsed
    return None


def _parse_codentum_result(body: str, task_id: str) -> AgentTeamsTaskResult | None:
    prefix = "CODENTUM_RESULT "
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith(prefix):
            continue
        raw = stripped[len(prefix) :].strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return AgentTeamsTaskResult(
                status="failed",
                detail="agentteams CODENTUM_RESULT marker is not valid JSON",
                reason_code=FailureCode.RUNTIME_ERROR,
            )
        if not isinstance(payload, dict):
            continue
        marker_task_id = str(payload.get("taskId") or payload.get("task_id") or "")
        if marker_task_id != task_id:
            continue
        status = str(payload.get("status") or "").strip().lower()
        if status not in {"completed", "failed"}:
            return AgentTeamsTaskResult(
                status="failed",
                detail=f"agentteams result marker has invalid status: {status or '(empty)'}",
                reason_code=FailureCode.RUNTIME_ERROR,
            )
        detail = str(payload.get("detail") or payload.get("summary") or "")
        if not detail:
            detail = "agentteams task completed" if status == "completed" else "agentteams task failed"
        result_status: Literal["completed", "failed"] = "completed" if status == "completed" else "failed"
        return AgentTeamsTaskResult(
            status=result_status,
            detail=detail,
            evidence=_evidence_refs(payload.get("evidence")),
            spent_cny=_float_field(payload.get("spentCny", payload.get("spent_cny")), default=0.0),
            touched_paths=_string_tuple(payload.get("touchedPaths", payload.get("touched_paths"))),
            reason_code=_failure_code(payload.get("reasonCode", payload.get("reason_code"))),
            metadata={"source": "matrix_result_marker"},
        )
    return None


def _failure_code(value: object) -> FailureCode:
    if isinstance(value, str) and value:
        try:
            return FailureCode(value)
        except ValueError:
            return FailureCode.RUNTIME_ERROR
    return FailureCode.RUNTIME_ERROR


def _evidence_refs(value: object) -> tuple[EvidenceRef, ...]:
    if not isinstance(value, list):
        return ()
    refs: list[EvidenceRef] = []
    for item in value:
        if isinstance(item, str) and item:
            refs.append(EvidenceRef(item))
    return tuple(refs)


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str) and item)


def _float_field(value: object, *, default: float) -> float:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


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


@dataclass(frozen=True, slots=True)
class _MatrixConfig:
    url: str
    domain: str
    admin_user: str
    admin_password: str


def _agentteams_env(explicit_env_file: str | None) -> dict[str, str]:
    env = dict(os.environ)
    for path in _agentteams_env_paths(explicit_env_file):
        if not path.is_file():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            if key and key not in env:
                env[key] = value.strip()
    return env


def _agentteams_env_paths(explicit_env_file: str | None) -> tuple[Path, ...]:
    paths: list[Path] = []
    if explicit_env_file:
        paths.append(Path(explicit_env_file).expanduser())
    paths.append(Path.cwd() / "agentteams-manager.env")
    paths.append(Path.home() / "agentteams-manager.env")
    return tuple(dict.fromkeys(paths))


def _quote_room_id(room_id: str) -> str:
    return quote(room_id, safe="")


def _matrix_txn_id(task_id: str, submitted_at: str) -> str:
    digest = hashlib.sha256(f"{task_id}:{submitted_at}".encode()).hexdigest()[:16]
    return f"codentum_{digest}"


def _summarize_process_failure(result: subprocess.CompletedProcess[str]) -> str:
    output = (result.stderr or result.stdout).strip()
    if output:
        return output
    return f"command exited with status {result.returncode}"
