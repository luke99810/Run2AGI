from __future__ import annotations

import asyncio
import json
from pathlib import Path

import codentum_harness.worker.team as team_worker
from codentum_contracts import (
    BudgetGrantRuntime,
    EvidenceRef,
    FailureCode,
    ModelRouting,
    PacketId,
    RoleSpec,
    SpawnRequest,
    WorkerCompleted,
    WorkerFailed,
)
from codentum_contracts.interfaces import WorkerEvent, WorkerHandle
from codentum_contracts.state import RoleSkill
from codentum_harness.runtime import TeamWorkerRuntimeConfig, build_team_worker_runtime
from codentum_harness.worker import (
    AgentTeamsDispatchReceipt,
    AgentTeamsTaskResult,
    AgentTeamsTaskSpec,
    AgentTeamsWorkerSpec,
    AgentTeamsWorkerStatus,
    TeamWorkerRuntime,
)


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


def test_team_settle_dispatches_task_and_collects_completed_result(tmp_path: Path) -> None:
    workspace = tmp_path / "workers" / "wp-abcdef"
    client = FakeAgentTeamsClient()
    runtime = TeamWorkerRuntime(repo_root=tmp_path, client=client, role_specs=(role_spec(),))

    handle = asyncio.run(runtime.spawn(request(workspace)))
    outcome = asyncio.run(runtime.settle(handle))

    assert isinstance(outcome, WorkerCompleted)
    assert outcome.status == "completed"
    assert tuple(outcome.evidence) == (
        "file:agentteams/status.json",
        "file:agentteams/dispatch.json",
        "file:agentteams/result.json",
        "file:agentteams/team-result.md",
    )
    assert outcome.spent_cny == 0.12
    assert outcome.touched_paths == ("src/app.py",)
    assert len(client.dispatched) == 1
    task = client.dispatched[0]
    assert task.task_id == "wp-abcdef-attempt-1"
    assert task.worker_name == "codentum-coder-wp-abcdef-a1"
    assert task.prompt_ref == "file:prompt/manifest.json"
    assert task.prompt_digest.startswith("sha256:")

    evidence_dir = workspace / ".codentum" / "evidence" / handle.worker_id
    dispatch = json.loads((evidence_dir / "agentteams" / "dispatch.json").read_text(encoding="utf-8"))
    result = json.loads((evidence_dir / "agentteams" / "result.json").read_text(encoding="utf-8"))
    assert dispatch["transport"] == "fake-matrix"
    assert result["status"] == "completed"
    assert result["spent_cny"] == 0.12

    events = asyncio.run(_collect_events(runtime, handle))
    assert [event.kind for event in events] == [
        "started",
        "checkpoint",
        "progress",
        "progress",
        "progress",
        "progress",
        "finished",
    ]
    assert events[3].payload["status_ref"] == "file:agentteams/status.json"
    assert events[3].payload["moduleId"] == "agentteams.worker"
    assert events[4].payload["moduleId"] == "agentteams.dispatch"
    assert events[4].payload["dispatch_ref"] == "file:agentteams/dispatch.json"
    assert events[5].payload["moduleId"] == "agentteams.result"
    assert events[5].payload["result_ref"] == "file:agentteams/result.json"
    assert events[6].payload["reason"] == "team_result_collected"
    assert events[6].payload["moduleState"] == "completed"


def test_team_settle_returns_failed_when_agentteams_result_fails(tmp_path: Path) -> None:
    workspace = tmp_path / "workers" / "wp-abcdef"
    client = FakeAgentTeamsClient(
        result=AgentTeamsTaskResult(
            status="failed",
            detail="worker reported blocked",
            reason_code=FailureCode.RUNTIME_ERROR,
            evidence=(EvidenceRef("file:agentteams/blocker.md"),),
            spent_cny=0.03,
        )
    )
    runtime = TeamWorkerRuntime(repo_root=tmp_path, client=client, role_specs=(role_spec(),))

    handle = asyncio.run(runtime.spawn(request(workspace)))
    outcome = asyncio.run(runtime.settle(handle))

    assert isinstance(outcome, WorkerFailed)
    assert outcome.detail == "worker reported blocked"
    assert outcome.reason_code == "runtime_error"
    assert tuple(outcome.evidence) == (
        "file:agentteams/status.json",
        "file:agentteams/dispatch.json",
        "file:agentteams/result.json",
        "file:agentteams/blocker.md",
    )
    assert outcome.spent_cny == 0.03
    events = asyncio.run(_collect_events(runtime, handle))
    assert events[-1].payload["reason"] == "team_result_failed"
    assert events[-1].payload["moduleState"] == "failed"


def test_agentteams_result_marker_parses_completed_result() -> None:
    payload: dict[str, object] = {
        "chunk": [
            {
                "content": {
                    "body": (
                        "manager note\n"
                        'CODENTUM_RESULT {"taskId":"wp-abcdef-attempt-1",'
                        '"status":"completed","detail":"done",'
                        '"spentCny":"0.05","touchedPaths":["src/app.py"],'
                        '"evidence":["file:agentteams/team-result.md"]}'
                    )
                }
            }
        ]
    }

    result = team_worker._extract_codentum_result(payload, "wp-abcdef-attempt-1")

    assert result is not None
    assert result.status == "completed"
    assert result.detail == "done"
    assert result.spent_cny == 0.05
    assert result.touched_paths == ("src/app.py",)
    assert result.evidence == ("file:agentteams/team-result.md",)


def test_team_spawn_reads_active_skill_prompt_from_project_shared_space(tmp_path: Path) -> None:
    workspace = tmp_path / "workers" / "wp-abcdef"
    _write_shared_skill(
        tmp_path / ".codentum" / "skills" / "shared",
        "frontend",
        "# Shared Frontend Skill\n\nUse the project shared copy.",
    )
    client = FakeAgentTeamsClient()
    runtime = TeamWorkerRuntime(
        repo_root=tmp_path,
        client=client,
        role_specs=(role_spec_with_frontend_skill(),),
    )

    handle = asyncio.run(runtime.spawn(request(workspace)))
    evidence_dir = workspace / ".codentum" / "evidence" / handle.worker_id
    prompt_manifest = json.loads(
        (evidence_dir / "prompt" / "manifest.json").read_text(encoding="utf-8")
    )
    system_prompt = (evidence_dir / "prompt" / "system.md").read_text(encoding="utf-8")

    assert prompt_manifest["skill_refs"] == ["frontend"]
    assert prompt_manifest["skill_source"] == "project_shared"
    assert "# Shared Frontend Skill" in system_prompt
    assert "# Frontend Skill" not in system_prompt


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


def role_spec_with_frontend_skill() -> RoleSpec:
    return RoleSpec(
        id="coder",
        usesModel=True,
        writes=("workspace/src/**",),
        reads=("packages/contracts/**",),
        tools=("read_file", "write_file"),
        transitions=(),
        skills=(RoleSkill(id="frontend", scope="role", state="active"),),
    )


def _write_shared_skill(root: Path, skill_id: str, body: str) -> None:
    skill_dir = root / skill_id
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "manifest.json").write_text(
        json.dumps({"id": skill_id, "version": "0.0.0"}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text(body + "\n", encoding="utf-8")


class FakeAgentTeamsClient:
    def __init__(
        self,
        *,
        create_error: Exception | None = None,
        result: AgentTeamsTaskResult | None = None,
    ) -> None:
        self.created: list[AgentTeamsWorkerSpec] = []
        self.dispatched: list[AgentTeamsTaskSpec] = []
        self.create_error = create_error
        self.statuses: dict[str, AgentTeamsWorkerStatus] = {}
        self.result = result or AgentTeamsTaskResult(
            status="completed",
            detail="team completed task",
            evidence=(EvidenceRef("file:agentteams/team-result.md"),),
            spent_cny=0.12,
            touched_paths=("src/app.py",),
        )

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

    def dispatch_task(self, spec: AgentTeamsTaskSpec) -> AgentTeamsDispatchReceipt:
        self.dispatched.append(spec)
        return AgentTeamsDispatchReceipt(
            task_id=spec.task_id,
            worker_name=spec.worker_name,
            transport="fake-matrix",
            target="!room:matrix-local.agentteams.io:18080",
            external_id="$event",
            submitted_at="2026-08-13T00:00:00+00:00",
            detail="fake dispatch accepted",
        )

    def collect_result(
        self,
        spec: AgentTeamsTaskSpec,
        receipt: AgentTeamsDispatchReceipt,
    ) -> AgentTeamsTaskResult:
        assert spec.task_id == receipt.task_id
        return self.result


async def _collect_events(runtime: TeamWorkerRuntime, handle: WorkerHandle) -> list[WorkerEvent]:
    return [event async for event in runtime.events(handle)]
