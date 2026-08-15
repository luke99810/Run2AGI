from __future__ import annotations

import asyncio
import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from shutil import which

import pytest
from codentum_contracts.state import RoleSkill
from codentum_contracts import (
    Acceptance,
    BudgetGrant,
    BudgetGrantRuntime,
    EvidenceRef,
    ModelRouting,
    PacketId,
    Provenance,
    RoleSpec,
    SpawnRequest,
    WorkerCompleted,
    WorkPacket,
    dump_state,
)
from codentum_contracts.interfaces import WorkerEvent
from codentum_harness.context_broker import ContextCandidate
from codentum_harness.worker import LocalWorkerRuntime, WorktreeIsolationError


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


def test_spawn_creates_isolated_git_worktree(git_repo: Path, tmp_path: Path) -> None:
    workspace = tmp_path / "workers" / "wp-abcdef"
    runtime = LocalWorkerRuntime(repo_root=git_repo)

    handle = asyncio.run(runtime.spawn(request(workspace)))

    assert handle.runtime_ref == str(workspace)
    assert (workspace / "README.md").read_text(encoding="utf-8") == "hello\n"
    assert Path(run_git(workspace, "rev-parse", "--show-toplevel").strip()) == workspace


def test_spawn_rejects_workspace_inside_controller_checkout(git_repo: Path) -> None:
    runtime = LocalWorkerRuntime(repo_root=git_repo)

    with pytest.raises(WorktreeIsolationError, match="outside repo root"):
        asyncio.run(runtime.spawn(request(git_repo / "nested-worker")))


def test_runtime_rejects_non_git_repo(tmp_path: Path) -> None:
    with pytest.raises(WorktreeIsolationError, match="not a git repository"):
        LocalWorkerRuntime(repo_root=tmp_path / "not-a-repo")


def test_settle_runs_injected_runner_after_workspace_exists(git_repo: Path, tmp_path: Path) -> None:
    workspace = tmp_path / "workers" / "wp-abcdef"

    def runner(req: SpawnRequest) -> WorkerCompleted:
        marker = Path(req.workspace) / "worker.txt"
        assert marker.parent.exists()
        prompt = (
            Path(req.workspace)
            / ".codentum"
            / "evidence"
            / f"{req.packet_id}-attempt-{req.attempt}"
            / "prompt"
            / "user.md"
        )
        assert prompt.exists()
        marker.write_text("ran\n", encoding="utf-8")
        return WorkerCompleted(
            evidence=(EvidenceRef("ev-worker"),),
            spent_cny=0.01,
            touched_paths=("worker.txt",),
        )

    runtime = LocalWorkerRuntime(repo_root=git_repo, runner=runner)
    outcome = asyncio.run(_spawn_and_settle(runtime, request(workspace)))

    assert outcome.status == "completed"
    assert (workspace / "worker.txt").read_text(encoding="utf-8") == "ran\n"


def test_events_are_replayable_by_sequence(git_repo: Path, tmp_path: Path) -> None:
    workspace = tmp_path / "workers" / "wp-abcdef"
    runtime = LocalWorkerRuntime(repo_root=git_repo)

    initial, later = asyncio.run(_collect_event_slices(runtime, request(workspace)))

    assert [event.kind for event in initial] == ["started", "checkpoint"]
    assert [event.kind for event in later] == ["finished"]


def test_spawn_writes_evidence_manifest_and_event_log(git_repo: Path, tmp_path: Path) -> None:
    workspace = tmp_path / "workers" / "wp-abcdef"
    runtime = LocalWorkerRuntime(repo_root=git_repo)

    handle = asyncio.run(runtime.spawn(request(workspace)))
    evidence_dir = workspace / ".codentum" / "evidence" / handle.worker_id

    manifest = json.loads((evidence_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["worker_id"] == handle.worker_id
    assert manifest["packet_id"] == "wp-abcdef"
    assert manifest["tools"] == ["read_file", "write_file"]
    assert (evidence_dir / "checkpoints" / "0000.json").exists()
    assert (evidence_dir / "prompt" / "manifest.json").exists()

    events = [
        json.loads(line)
        for line in (evidence_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [event["kind"] for event in events] == ["started", "checkpoint"]
    assert events[1]["payload"]["path"] == "checkpoints/0000.json"


def test_spawn_mirrors_evidence_to_project_state_dir(git_repo: Path, tmp_path: Path) -> None:
    """C 读取主项目 .codentum/evidence，不读取隔离 worktree。"""
    workspace = tmp_path / "workers" / "wp-abcdef"
    runtime = LocalWorkerRuntime(
        repo_root=git_repo,
        project_state_dir=git_repo / ".codentum",
    )

    handle = asyncio.run(runtime.spawn(request(workspace)))
    primary = workspace / ".codentum" / "evidence" / handle.worker_id
    mirror = git_repo / ".codentum" / "evidence" / handle.worker_id

    assert json.loads((mirror / "manifest.json").read_text(encoding="utf-8"))["worker_id"] == (
        handle.worker_id
    )
    assert (mirror / "events.jsonl").read_text(encoding="utf-8") == (
        primary / "events.jsonl"
    ).read_text(encoding="utf-8")
    assert (mirror / "checkpoints" / "0000.json").exists()
    assert (mirror / "prompt" / "manifest.json").exists()


def test_spawn_fills_empty_tools_from_rolespec(git_repo: Path, tmp_path: Path) -> None:
    workspace = tmp_path / "workers" / "wp-abcdef"
    empty_tools_req = request(workspace)
    empty_tools_req = SpawnRequest(
        packet_id=empty_tools_req.packet_id,
        role=empty_tools_req.role,
        mounts=empty_tools_req.mounts,
        tools=(),
        routing=empty_tools_req.routing,
        budget=empty_tools_req.budget,
        workspace=empty_tools_req.workspace,
        attempt=empty_tools_req.attempt,
    )
    runtime = LocalWorkerRuntime(repo_root=git_repo, role_specs=(role_spec(),))

    handle = asyncio.run(runtime.spawn(empty_tools_req))
    evidence_dir = workspace / ".codentum" / "evidence" / handle.worker_id

    manifest = json.loads((evidence_dir / "manifest.json").read_text(encoding="utf-8"))
    user_prompt = (evidence_dir / "prompt" / "user.md").read_text(encoding="utf-8")
    assert manifest["tools"] == ["read_file", "write_file"]
    assert "- read_file" in user_prompt
    assert "- write_file" in user_prompt


def test_spawn_reads_active_skill_prompt_from_project_shared_space(
    git_repo: Path,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workers" / "wp-abcdef"
    _write_shared_skill(
        git_repo / ".codentum" / "skills" / "shared",
        "frontend",
        "# Shared Frontend Skill\n\nPrefer project shared UI rules.",
    )
    runtime = LocalWorkerRuntime(repo_root=git_repo, role_specs=(role_spec_with_frontend_skill(),))

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


def test_spawn_uses_role_spec_resolver_before_prompt_bundle(
    git_repo: Path,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workers" / "wp-abcdef"
    _write_shared_skill(
        git_repo / ".codentum" / "skills" / "shared",
        "local-dynamic-test",
        "# Dynamic Local Skill\n\nUse the uploaded project skill.",
    )

    def resolve(req: SpawnRequest, spec: RoleSpec) -> RoleSpec:
        assert req.packet_id == "wp-abcdef"
        return spec.model_copy(
            update={
                "skills": (
                    *(spec.skills or ()),
                    RoleSkill(id="local-dynamic-test", scope="role", state="active"),
                )
            }
        )

    runtime = LocalWorkerRuntime(
        repo_root=git_repo,
        role_specs=(role_spec(),),
        role_spec_resolver=resolve,
    )

    handle = asyncio.run(runtime.spawn(request(workspace)))
    evidence_dir = workspace / ".codentum" / "evidence" / handle.worker_id
    prompt_manifest = json.loads(
        (evidence_dir / "prompt" / "manifest.json").read_text(encoding="utf-8")
    )
    system_prompt = (evidence_dir / "prompt" / "system.md").read_text(encoding="utf-8")

    assert prompt_manifest["skill_refs"] == ["local-dynamic-test"]
    assert "# Dynamic Local Skill" in system_prompt


def test_spawn_injects_packet_intent_from_workpacket_file(git_repo: Path, tmp_path: Path) -> None:
    workspace = tmp_path / "workers" / "wp-abcdef"
    _write_packet(
        git_repo,
        WorkPacket(
            id=PacketId("wp-abcdef"),
            kind="impl",
            state="pending",
            role="coder",
            ownsPaths=("src/app/",),
            readsPaths=("tests/",),
            deps=(),
            acceptance=Acceptance(
                kind= "test",
                predicate= "pytest tests/acceptance/test_app.py",
                authoredBy= "qa",
            ),
            budget=BudgetGrant(
                currency= "CNY",
                limitCny= 1.0,
                spentCny= 0.0,
                degradationChain= ("summary",),
            ),
            attempts=0,
            evidence=(),
            provenance=Provenance(createdBy= "planner", createdAt= "2026-08-10T00:00:00Z"),
        ),
    )
    runtime = LocalWorkerRuntime(repo_root=git_repo, role_specs=(role_spec(),))

    handle = asyncio.run(runtime.spawn(request(workspace)))
    evidence_dir = workspace / ".codentum" / "evidence" / handle.worker_id
    user_prompt = (evidence_dir / "prompt" / "user.md").read_text(encoding="utf-8")

    assert "### packet-intent" in user_prompt
    assert "pytest tests/acceptance/test_app.py" in user_prompt
    assert "ownsPaths: src/app/" in user_prompt
    assert "Visible Context\n\n- (none)" not in user_prompt


def test_spawn_falls_back_to_request_intent_when_packet_file_is_missing(
    git_repo: Path,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workers" / "wp-abcdef"
    runtime = LocalWorkerRuntime(repo_root=git_repo, role_specs=(role_spec(),))

    handle = asyncio.run(runtime.spawn(request(workspace)))
    evidence_dir = workspace / ".codentum" / "evidence" / handle.worker_id
    user_prompt = (evidence_dir / "prompt" / "user.md").read_text(encoding="utf-8")

    assert "### packet-intent" in user_prompt
    assert "Source: SpawnRequest fallback" in user_prompt
    assert "Visible Context\n\n- (none)" not in user_prompt


def test_spawn_prepares_context_into_checkpoint_with_single_public_entrypoint(
    git_repo: Path,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workers" / "wp-abcdef"

    def load_context(req: SpawnRequest, spec: RoleSpec) -> tuple[ContextCandidate, ...]:
        assert req.packet_id == "wp-abcdef"
        assert spec.id == "coder"
        return (
            ContextCandidate(
                ref="packet",
                artifact_path=".codentum/backlog/packets/wp-abcdef.yaml",
                text="implement app shell",
                required=True,
                priority=1,
            ),
        )

    runtime = LocalWorkerRuntime(
        repo_root=git_repo,
        context_loader=load_context,
        context_char_budget=100,
    )

    handle = asyncio.run(runtime.spawn(request(workspace)))

    checkpoint = json.loads(
        (
            workspace / ".codentum" / "evidence" / handle.worker_id / "checkpoints" / "0000.json"
        ).read_text(encoding="utf-8")
    )
    assert {slice_["ref"] for slice_ in checkpoint["context"]["slices"]} == {"packet-intent", "packet"}


async def _spawn_and_settle(runtime: LocalWorkerRuntime, req: SpawnRequest) -> WorkerCompleted:
    handle = await runtime.spawn(req)
    outcome = await runtime.settle(handle)
    assert isinstance(outcome, WorkerCompleted)
    return outcome


async def _collect_event_slices(
    runtime: LocalWorkerRuntime,
    req: SpawnRequest,
) -> tuple[list[WorkerEvent], list[WorkerEvent]]:
    handle = await runtime.spawn(req)
    initial = [event async for event in runtime.events(handle)]
    await runtime.settle(handle)
    later = [event async for event in runtime.events(handle, since_seq=len(initial))]
    return initial, later


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


def _write_packet(repo: Path, packet: WorkPacket) -> None:
    packets_dir = repo / ".codentum" / "packets"
    packets_dir.mkdir(parents=True, exist_ok=True)
    (packets_dir / f"{packet.id}.json").write_text(
        json.dumps(dump_state(packet), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ══════════════════════════════════════════════════════════════
#  空仓库：打开一个新文件夹就提需求，是最基本的用户流程
#
#  ★ 上面那个 git_repo 夹具**每次都建了一个提交** —— 正是它让这个缺陷
#    一直没被发现。夹具假设了顺利路径，于是「用户刚 git init 的空目录」
#    这条真实路径从来没有被测过。
#
#    实测现象（2026-08-15 生成命中账本时撞到）：
#      spawn 失败，packet wp-xxx 保持 ready 并释放锁：
#      WorktreeIsolationError: fatal: invalid reference: HEAD
#
#    后果不是崩溃，是 packet 永远停在 ready，而用户只看到「什么都没发生」。
# ══════════════════════════════════════════════════════════════


@pytest.fixture
def empty_git_repo(tmp_path: Path) -> Path:
    """刚 `git init`、**一个提交都没有**的仓库 —— 新项目的真实起点。"""

    repo = tmp_path / "fresh"
    repo.mkdir()
    run_git(repo, "init")
    run_git(repo, "config", "user.name", "tester")
    run_git(repo, "config", "user.email", "tester@example.com")
    return repo


def test_worktree_on_a_repo_without_commits_says_what_to_do(
    empty_git_repo: Path, tmp_path: Path
) -> None:
    """★ 错误必须**说清怎么办**，而不是把 git 的原文抛给用户。

    `fatal: invalid reference: HEAD` 对写代码的人都不够直白，
    对「打开一个空文件夹提需求」的使用者更是完全无从下手。
    """

    from codentum_harness.worker.worktree import GitWorktreeManager, WorktreeIsolationError

    isolation = GitWorktreeManager(empty_git_repo)
    with pytest.raises(WorktreeIsolationError) as excinfo:
        isolation.create(tmp_path / "workers" / "wp-1")

    message = str(excinfo.value)
    assert "没有任何提交" in message, f"错误没说清根因：{message}"
    assert "ensure_project_initialized" in message, "错误没给出可执行的下一步"


def test_ensure_project_initialized_creates_the_first_commit(empty_git_repo: Path) -> None:
    """空仓库 → 建一个空的初始提交，之后 worktree 可用。"""

    from codentum_harness.worker.worktree import ensure_project_initialized

    result = ensure_project_initialized(empty_git_repo)

    assert result.changed is True
    assert run_git(empty_git_repo, "rev-parse", "HEAD").strip()


def test_ensure_project_initialized_never_touches_a_repo_with_history(git_repo: Path) -> None:
    """★ 这条是安全判据，不是功能判据。

    在别人已有的仓库历史上加一个提交，是**不可接受的副作用** ——
    使用者把 Codentum 指向一个真实项目时，它绝不能往那段历史里写东西。
    只有「一个提交都没有」的仓库才允许被初始化。
    """

    from codentum_harness.worker.worktree import ensure_project_initialized

    before = run_git(git_repo, "rev-parse", "HEAD").strip()
    log_before = run_git(git_repo, "log", "--oneline").strip()

    result = ensure_project_initialized(git_repo)

    assert result.changed is False, "动了一个已有历史的仓库"
    assert run_git(git_repo, "rev-parse", "HEAD").strip() == before
    assert run_git(git_repo, "log", "--oneline").strip() == log_before


def test_ensure_project_initialized_inits_a_plain_folder(tmp_path: Path) -> None:
    """连 .git 都没有的普通文件夹 —— 桌面端「打开一个新目录」的最常见形态。"""

    from codentum_harness.worker.worktree import ensure_project_initialized

    plain = tmp_path / "plain"
    plain.mkdir()

    result = ensure_project_initialized(plain)

    assert result.changed is True
    assert (plain / ".git").exists()
    assert run_git(plain, "rev-parse", "HEAD").strip()


def test_spawn_works_after_initialization(empty_git_repo: Path, tmp_path: Path) -> None:
    """★ 端到端：初始化之后，最基本的那条路径真的走得通。

    前面几条测的是各段；这条测的是「打开空文件夹 → 提需求」不再卡死。
    """

    from codentum_harness.worker.worktree import ensure_project_initialized

    ensure_project_initialized(empty_git_repo)
    workspace = tmp_path / "workers" / "wp-abcdef"
    runtime = LocalWorkerRuntime(repo_root=empty_git_repo)

    handle = asyncio.run(runtime.spawn(request(workspace)))
    assert handle.runtime_ref == str(workspace)
