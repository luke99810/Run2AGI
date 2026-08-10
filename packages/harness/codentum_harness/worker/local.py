"""Local WorkerRuntime implementation.

This is the P0 shell: prepare an isolated Git worktree, expose runtime events,
and delegate the actual model phase to an injectable runner. No control-plane
imports, no retry logic, and no state transitions happen here.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

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
from codentum_contracts.state import RoleId, RoleSpec
from codentum_roles import load_builtin_role_specs

from codentum_harness.checkpoint import write_initial_checkpoint
from codentum_harness.context_broker import ContextBundle, ContextCandidate, assemble_context_bundle
from codentum_harness.prepare import PreparedExecution
from codentum_harness.prompt_bundle import WorkerPromptBundle, write_worker_prompt_bundle

from .worktree import GitWorktreeManager

__all__ = [
    "LocalWorkerRuntime",
    "WorkerContextLoader",
    "WorkerRunner",
]

WorkerRunner = Callable[[SpawnRequest], WorkerOutcome]
WorkerContextLoader = Callable[[SpawnRequest, RoleSpec], tuple[ContextCandidate, ...]]


class LocalWorkerRuntime:
    """Local implementation of the frozen WorkerRuntime protocol."""

    def __init__(
        self,
        *,
        repo_root: Path | str,
        runner: WorkerRunner | None = None,
        role_specs: tuple[RoleSpec, ...] | None = None,
        context_loader: WorkerContextLoader | None = None,
        context_char_budget: int | None = None,
    ) -> None:
        if context_loader is not None and context_char_budget is None:
            raise ValueError("context_char_budget is required when context_loader is provided")

        self._worktrees = GitWorktreeManager(repo_root)
        self._runner = runner
        specs = load_builtin_role_specs() if role_specs is None else role_specs
        self._role_specs = {spec.id: spec for spec in specs}
        self._context_loader = context_loader
        self._context_char_budget = context_char_budget
        self._sessions: dict[str, _Session] = {}

    async def spawn(self, req: SpawnRequest) -> WorkerHandle:
        prepared = self._prepare(req)
        return await self._spawn(prepared)

    def _prepare(self, req: SpawnRequest) -> PreparedExecution:
        spec = self._load_role_spec(req.role)
        effective_req = req if req.tools else replace(req, tools=tuple(spec.tools))
        context = None
        if self._context_loader is not None:
            assert self._context_char_budget is not None
            context = assemble_context_bundle(
                spec,
                candidates=self._context_loader(effective_req, spec),
                char_budget=self._context_char_budget,
            )
        return PreparedExecution(
            request=effective_req,
            role_spec=spec,
            tools=tuple(effective_req.tools),
            mount_paths=tuple(m.mount_path for m in effective_req.mounts),
            context=context,
        )

    async def _spawn(self, prepared: PreparedExecution) -> WorkerHandle:
        req = prepared.request
        workspace = self._worktrees.create(req.workspace)
        worker_id = f"{req.packet_id}-attempt-{req.attempt}"
        if worker_id in self._sessions:
            raise RuntimeError(f"worker already exists: {worker_id}")

        handle = WorkerHandle(
            worker_id=worker_id,
            packet_id=req.packet_id,
            role=req.role,
            runtime_ref=str(workspace),
        )
        evidence_dir = workspace / ".codentum" / "evidence" / worker_id
        session = _Session(handle=handle, request=req, evidence_dir=evidence_dir)
        session.write_manifest(workspace)
        checkpoint = session.write_checkpoint0(prepared.context)
        session.write_prompt_bundle(prepared.role_spec, prepared.context)
        session.append(
            "started",
            {
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
        self._sessions[worker_id] = session

        if self._runner is not None:
            session.task = asyncio.create_task(self._run(session))
        return handle

    def _load_role_spec(self, role: RoleId) -> RoleSpec:
        spec = self._role_specs.get(role)
        if spec is None:
            raise RuntimeError(f"RoleSpec is not loaded for role: {role}")
        return spec

    async def events(self, handle: WorkerHandle, since_seq: int = 0) -> AsyncIterator[WorkerEvent]:
        session = self._get(handle)
        for event in session.events:
            if event.seq > since_seq:
                yield event

    async def settle(self, handle: WorkerHandle) -> WorkerOutcome:
        session = self._get(handle)
        if session.outcome is not None:
            return session.outcome

        if session.task is None:
            session.outcome = WorkerFailed(
                reason_code=FailureCode.RUNTIME_ERROR,
                detail="no worker runner configured",
                evidence=(),
                spent_cny=0.0,
            )
            session.append("finished", {"status": session.outcome.status})
            return session.outcome

        session.outcome = await session.task
        return session.outcome

    async def abort(self, handle: WorkerHandle, reason: AbortReason) -> None:
        session = self._get(handle)
        if session.task is not None and not session.task.done():
            session.task.cancel()
        session.outcome = WorkerAborted(reason=reason, spent_cny=0.0)
        session.append("finished", {"status": session.outcome.status, "reason": reason})

    async def resume(self, ref: CheckpointRef) -> WorkerHandle:
        raise NotImplementedError(f"checkpoint resume is not implemented yet: {ref.worker_id}")

    async def adopt(self, runtime_ref: str) -> WorkerHandle | None:
        for session in self._sessions.values():
            if session.handle.runtime_ref == runtime_ref:
                return session.handle
        return None

    async def _run(self, session: _Session) -> WorkerOutcome:
        assert self._runner is not None
        try:
            outcome = await asyncio.to_thread(self._runner, session.request)
        except Exception as exc:
            outcome = WorkerFailed(
                reason_code=FailureCode.RUNTIME_ERROR,
                detail=str(exc),
                evidence=(),
                spent_cny=0.0,
            )
        session.append("finished", {"status": outcome.status})
        return outcome

    def _get(self, handle: WorkerHandle) -> _Session:
        session = self._sessions.get(handle.worker_id)
        if session is None:
            raise KeyError(f"unknown worker: {handle.worker_id}")
        return session


class _Session:
    def __init__(self, *, handle: WorkerHandle, request: SpawnRequest, evidence_dir: Path) -> None:
        self.handle = handle
        self.request = request
        self.evidence_dir = evidence_dir
        self.events_file = evidence_dir / "events.jsonl"
        self.events: list[WorkerEvent] = []
        self.task: asyncio.Task[WorkerOutcome] | None = None
        self.outcome: WorkerOutcome | None = None

    def write_manifest(self, workspace: Path) -> None:
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
