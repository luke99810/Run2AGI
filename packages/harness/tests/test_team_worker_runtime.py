from __future__ import annotations

import asyncio
import json
from pathlib import Path

from codentum_contracts import (
    BudgetGrantRuntime,
    ModelRouting,
    PacketId,
    RoleSpec,
    SpawnRequest,
    WorkerFailed,
)
from codentum_contracts.interfaces import WorkerEvent, WorkerHandle
from codentum_harness.runtime import TeamWorkerRuntimeConfig, build_team_worker_runtime
from codentum_harness.worker import AgentTeamsWorkerSpec, AgentTeamsWorkerStatus, TeamWorkerRuntime


def request(workspace: Path) -> SpawnRequest:
    return SpawnRequest(
        packet_id=PacketId("wp-abcdef"),
        role="coder",
        mounts=(),
        tools=("read_file", "write_file"),
        routing=ModelRouting(model="qwen3.6-plus", effort="medium"),
        budget=BudgetGrantRuntime(limit_cny=1.0, degradation_chain=()),
        workspace=str(workspace),
        attempt=1,
    )


def test_team_spawn_creates_agentteams_worker_and_prompt_bundle(tmp_path: Path) -> None:
    workspace = tmp_path / "workers" / "wp-abcdef"
    client = FakeAgentTeamsClient()
    runtime = TeamWorkerRuntime(repo_root=tmp_path, client=client, role_specs=(role_spec(),))

    handle = asyncio.run(runtime.spawn(request(workspace)))

    assert handle.runtime_ref == "agentteams://worker/codentum-coder-wp-abcdef-a1"
    assert client.created == [
        AgentTeamsWorkerSpec(
            name="codentum-coder-wp-abcdef-a1",
            model="qwen3.6-plus",
            runtime="copaw",
            identity="Codentum coder worker for packet wp-abcdef.",
            wait_timeout_seconds=300.0,
        )
    ]

    evidence_dir = workspace / ".codentum" / "evidence" / handle.worker_id
    manifest = json.loads((evidence_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["runtime_mode"] == "agentteams"
    assert manifest["agentteams"]["worker"] == "codentum-coder-wp-abcdef-a1"
    assert (evidence_dir / "checkpoints" / "0000.json").exists()
    assert (evidence_dir / "prompt" / "manifest.json").exists()
    assert (evidence_dir / "agentteams" / "status.json").exists()

    events = asyncio.run(_collect_events(runtime, handle))
    assert [event.kind for event in events] == ["started", "checkpoint", "progress"]
    assert events[2].payload == {
        "runtime_mode": "agentteams",
        "moduleId": "agentteams.worker",
        "moduleLabel": "AgentTeams Worker",
        "moduleState": "running",
        "agentteams_worker": "codentum-coder-wp-abcdef-a1",
        "phase": "Running",
        "model": "qwen3.6-plus",
        "runtime": "copaw",
        "status_ref": "file:agentteams/status.json",
        "container_state": "running",
        "matrix_user_id": "@codentum-coder-wp-abcdef-a1:matrix-local.agentteams.io:18080",
        "room_id": "!room:matrix-local.agentteams.io:18080",
        "message": "backend=docker status=running",
    }


def test_team_settle_fails_closed_until_dispatch_is_implemented(tmp_path: Path) -> None:
    workspace = tmp_path / "workers" / "wp-abcdef"
    client = FakeAgentTeamsClient()
    runtime = TeamWorkerRuntime(repo_root=tmp_path, client=client, role_specs=(role_spec(),))

    handle = asyncio.run(runtime.spawn(request(workspace)))
    outcome = asyncio.run(runtime.settle(handle))

    assert isinstance(outcome, WorkerFailed)
    assert outcome.status == "failed"
    assert "task dispatch and result collection are not implemented yet" in outcome.detail
    assert tuple(outcome.evidence) == ("file:agentteams/status.json",)

    events = asyncio.run(_collect_events(runtime, handle))
    assert [event.kind for event in events] == ["started", "checkpoint", "progress", "progress", "finished"]
    assert events[3].payload["status_ref"] == "file:agentteams/status.json"
    assert events[3].payload["moduleId"] == "agentteams.worker"
    assert events[4].payload["reason"] == "team_dispatch_missing"
    assert events[4].payload["moduleState"] == "failed"


def test_team_spawn_records_agentteams_error_as_failed_outcome(tmp_path: Path) -> None:
    workspace = tmp_path / "workers" / "wp-abcdef"
    client = FakeAgentTeamsClient(create_error=RuntimeError("docker daemon unavailable"))
    runtime = TeamWorkerRuntime(repo_root=tmp_path, client=client, role_specs=(role_spec(),))

    handle = asyncio.run(runtime.spawn(request(workspace)))
    outcome = asyncio.run(runtime.settle(handle))

    assert isinstance(outcome, WorkerFailed)
    assert "agentteams worker provisioning failed" in outcome.detail
    assert "docker daemon unavailable" in outcome.detail
    evidence_dir = workspace / ".codentum" / "evidence" / handle.worker_id
    error = json.loads((evidence_dir / "agentteams" / "error.json").read_text(encoding="utf-8"))
    assert error["error_type"] == "RuntimeError"
    events = asyncio.run(_collect_events(runtime, handle))
    assert events[-1].payload["moduleId"] == "agentteams.worker"
    assert events[-1].payload["moduleState"] == "failed"
    assert events[-1].payload["error_ref"] == "file:agentteams/error.json"


def test_build_team_worker_runtime_wires_injected_client(tmp_path: Path) -> None:
    workspace = tmp_path / "workers" / "wp-abcdef"
    client = FakeAgentTeamsClient()
    runtime = build_team_worker_runtime(
        TeamWorkerRuntimeConfig(repo_root=tmp_path, worker_name_prefix="codentum.test"),
        role_specs=(role_spec(),),
        client=client,
    )

    handle = asyncio.run(runtime.spawn(request(workspace)))

    assert handle.runtime_ref == "agentteams://worker/codentum-test-coder-wp-abcdef-a1"
    assert client.created[0].name == "codentum-test-coder-wp-abcdef-a1"


def role_spec() -> RoleSpec:
    return RoleSpec(
        id="coder",
        usesModel=True,
        writes=("workspace/src/**",),
        reads=("packages/contracts/**",),
        tools=("read_file", "write_file"),
        transitions=(),
    )


class FakeAgentTeamsClient:
    def __init__(self, *, create_error: Exception | None = None) -> None:
        self.created: list[AgentTeamsWorkerSpec] = []
        self.create_error = create_error
        self.statuses: dict[str, AgentTeamsWorkerStatus] = {}

    def create_worker(self, spec: AgentTeamsWorkerSpec) -> None:
        if self.create_error is not None:
            raise self.create_error
        self.created.append(spec)
        self.statuses[spec.name] = AgentTeamsWorkerStatus(
            name=spec.name,
            phase="Running",
            model=spec.model,
            runtime=spec.runtime,
            container_state="running",
            matrix_user_id=f"@{spec.name}:matrix-local.agentteams.io:18080",
            room_id="!room:matrix-local.agentteams.io:18080",
            message="backend=docker status=running",
        )

    def worker_status(self, name: str) -> AgentTeamsWorkerStatus:
        return self.statuses[name]


async def _collect_events(runtime: TeamWorkerRuntime, handle: WorkerHandle) -> list[WorkerEvent]:
    return [event async for event in runtime.events(handle)]
