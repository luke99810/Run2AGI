"""引擎服务的判据。

★ 每条测试都对应一个「如果它坏了会怎样」，不是为了覆盖率。
  尤其是能力表那几条：报错一个 true，桌面端就会显示一个点了没反应的按钮，
  而那种缺陷没有任何东西会报错。
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from codentum_delivery.protocol import CAPABILITY_NAMES, validate_handshake, validate_receipt
from codentum_engine.intake import (
    build_packet_for_requirement,
    choose_acceptance_author,
    new_packet_id,
)
from codentum_engine.service import IMPLEMENTED_CAPABILITIES, EngineConfig, EngineService
from codentum_engine.session import EngineSession
from codentum_harness.worker import TeamWorkerRuntime

_KEY_ENVS = ("DASHSCOPE_API_KEY", "BAILIAN_API_KEY", "QWEN_API_KEY", "AGENTTEAMS_LLM_API_KEY")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    return root


@pytest.fixture
def no_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """本机可能真的配了 Key。不清掉的话，这组测试会在有 Key 的机器上
    走另一条分支 —— 那正是「在我机器上是绿的」的经典成因。"""

    for name in _KEY_ENVS:
        monkeypatch.delenv(name, raising=False)
    yield


@pytest.fixture
def fake_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in _KEY_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "REPLACE_ME_DASHSCOPE_API_KEY_FOR_TESTS")
    yield


def _service(project: Path, **kw: object) -> EngineService:
    return EngineService(EngineConfig(project_root=project, **kw))  # type: ignore[arg-type]


def _command(action: str, run_id: str, project: Path, **payload: object) -> dict[str, object]:
    return {
        "commandId": f"cmd-{action}-1",
        "runId": run_id,
        "expectedRevision": 0,
        "target": {"agentId": "operator"},
        "action": action,
        "payload": {"projectRoot": str(project), **payload},
        "requestedAt": "2026-08-10T12:00:00.000Z",
    }


# ══════════════════════════════════════════════════════════════
#  握手
# ══════════════════════════════════════════════════════════════


def test_handshake_satisfies_the_delivery_contract(project: Path, fake_key: None) -> None:
    """★ 用 C 那边真正会跑的校验器验，不是自己写一遍断言。

    validate_handshake 是 fail-closed 的：少一个 capability、多一个
    capability、或者某个值不是 bool，都会被拒。
    """

    handshake = _service(project).handshake()
    validated = validate_handshake(handshake)
    assert validated["connected"] is True
    assert validated["runId"]


def test_unimplemented_capabilities_are_reported_false(project: Path, fake_key: None) -> None:
    """★ 假引擎把 9 个能力全报 true。真引擎照抄的话，桌面端会显示一排
    点下去没反应的按钮 —— 而不会有任何东西报错。"""

    capabilities = _service(project).handshake()["capabilities"]
    assert isinstance(capabilities, dict)
    assert set(capabilities) == set(CAPABILITY_NAMES)

    enabled = {name for name, value in capabilities.items() if value}
    assert enabled == set(IMPLEMENTED_CAPABILITIES)

    # 反向：真报了 true 的那个必须真的能走通（下面 submit 的测试证明）
    assert "requirements" in enabled


def test_without_a_model_key_requirements_is_not_advertised(project: Path, no_key: None) -> None:
    """★ 没有 Key 的话，packet 建出来也不会被执行。

    如果这里报 requirements=true，用户提交后看到的是「命令被接受了，
    然后什么都不发生」—— 最难查的一种成功。
    """

    handshake = _service(project).handshake()
    validate_handshake(handshake)
    capabilities = handshake["capabilities"]
    assert isinstance(capabilities, dict)
    assert capabilities["requirements"] is False
    assert "unavailableReason" in handshake


def test_worker_runtime_mode_team_selects_team_runtime(project: Path, fake_key: None) -> None:
    """Team-mode 不是测试孤岛；生产装配能真的选到 TeamWorkerRuntime。"""

    service = _service(project, worker_runtime_mode="team")
    runtime = service._build_worker_runtime()

    assert isinstance(runtime, TeamWorkerRuntime)


def test_state_revision_survives_a_restart(project: Path, fake_key: None) -> None:
    """★ 网关判 `revision < 上一次` 为 non_monotonic_state_revision 并拒绝。

    假引擎的进程内计数器一重启就归零，于是重启后第一条回执会被网关判为
    协议违规。这条测试盯的就是那个。
    """

    first = _service(project)
    before = first.revision
    first._session.bump()
    bumped = first.revision
    assert bumped == before + 1

    second = _service(project)  # 新进程会做的事：重新 load
    assert second.revision == bumped
    assert second.run_id == first.run_id


def test_corrupt_session_file_is_loud(project: Path, fake_key: None) -> None:
    """★ 静默重建一个新 runId，桌面端手上的旧 runId 会被网关判 run_mismatch，
    用户看到的现象是「按钮全都没反应」。宁可炸。"""

    _service(project)
    (project / ".codentum" / "engine-session.json").write_text("{ not json", encoding="utf-8")
    with pytest.raises(ValueError, match="runId"):
        EngineSession.load_or_create(project / ".codentum")


# ══════════════════════════════════════════════════════════════
#  submit_requirement
# ══════════════════════════════════════════════════════════════


def test_submit_requirement_creates_a_packet_and_returns_accepted(
    project: Path, fake_key: None
) -> None:
    """★ 断言 `accepted` 而不是 `applied`。

    网关默认超时 8 秒，真实模型 packet 要跑几十秒到几分钟。
    回 applied 等于在结果出来之前宣布结果。
    """

    service = _service(project)
    receipt = service.command(
        _command("submit_requirement", service.run_id, project, requirement="做一个订阅费用管理器")
    )
    validate_receipt(receipt, "cmd-submit_requirement-1")
    assert receipt["status"] == "accepted"

    packets = service.packets()
    assert len(packets) == 1
    packet = next(iter(packets.values()))
    assert packet.state in {"pending", "ready", "running", "review", "accepted", "blocked"}
    assert packet.role == "coder"
    # ★ routing 必须填。不填的话 A 的 _build_spawn_request 退回 model="default"，
    #   而 "default" 在百炼不存在 —— 现象是 provider 报错，像是「模型连不上」。
    assert packet.routing is not None
    assert packet.routing.model


def test_requirement_text_is_persisted_verbatim(project: Path, fake_key: None) -> None:
    """★ 需求原文是模型唯一能知道「要做什么」的来源（契约里没有这个字段）。
    丢了它，模型收到的就是 08-10 那份 `Visible Context: (none)`。"""

    service = _service(project)
    text = "实现一个可以管理订阅费用的软件，含到期提醒"
    service.command(_command("submit_requirement", service.run_id, project, requirement=text))

    saved = list((project / ".codentum" / "requirements").glob("*.json"))
    assert len(saved) == 1
    record = json.loads(saved[0].read_text("utf-8"))
    assert record["text"] == text


def test_requirement_reaches_the_model_context(project: Path, fake_key: None) -> None:
    """★ 存下来还不够 —— 得真的能被 context_loader 取回来交给 Harness。

    这两件事之间断掉的话，`requirements/` 里躺着完整的需求，
    模型收到的仍然是空上下文。
    """

    service = _service(project)
    service.command(
        _command("submit_requirement", service.run_id, project, requirement="做一个记账小工具")
    )
    packet_id = next(iter(service.packets()))

    class _Request:
        pass

    request = _Request()
    request.packet_id = packet_id  # type: ignore[attr-defined]

    candidates = service._context_loader(request, service._role_specs[0])
    assert len(candidates) == 1
    assert "记账小工具" in candidates[0].text
    # required=True：被 char_budget 裁掉的话，模型又会收到一份没有任务的 prompt
    assert candidates[0].required is True


def test_knowledge_resource_selection_is_indexed_into_memory_context(
    project: Path,
    fake_key: None,
) -> None:
    """★ C 已经把知识资源放进 resourceSelections；B 必须真的消费它。

    这条不是证明 RAG 完整产品化，而是钉住最关键的运行时事实：
    本地知识文件会进入 MemoryIndex，生成 indexVersion，并作为 ContextCandidate
    进入 Harness，而不是只躺在 requirement payload 里。
    """

    knowledge = project / "domain-notes.md"
    knowledge.write_text("订阅费用页面必须展示 CNY 成本归因和模型用量。\n", encoding="utf-8")
    service = _service(project)
    service.command(
        _command(
            "submit_requirement",
            service.run_id,
            project,
            requirement="实现订阅费用页面",
            resourceSelectionContract="codentum.resource-selection.v1",
            resourceSelections=[
                {
                    "id": "managed:00000000-0000-0000-0000-000000000001",
                    "kind": "knowledge",
                    "scope": "project",
                    "sourceKind": "file",
                    "localPath": str(knowledge),
                }
            ],
        )
    )
    packet_id = next(iter(service.packets()))

    class _Request:
        pass

    request = _Request()
    request.packet_id = packet_id  # type: ignore[attr-defined]

    candidates = service._context_loader(request, service._role_specs[0])
    memory_candidates = [candidate for candidate in candidates if candidate.ref.startswith("memory:")]

    assert memory_candidates, "知识资源没有进入 MemoryIndex context"
    assert "indexVersion: sha256:" in memory_candidates[0].text
    assert "CNY 成本归因" in memory_candidates[0].text
    assert list((project / ".codentum" / "memory" / "index" / "entries").glob("*.json"))
    projection = json.loads(
        (project / ".codentum" / "memory" / "projection.json").read_text(encoding="utf-8")
    )
    assert projection["packetId"] == str(packet_id)
    assert projection["indexVersion"].startswith("sha256:")
    assert projection["sourceCount"] == 1
    assert projection["indexedRefCount"] == 1
    assert projection["retrievalCount"] >= 1
    assert projection["retrievals"][0]["category"] == "knowledge"
    assert projection["retrievals"][0]["memoryRef"].startswith("mem:sha256:")


def test_unknown_payload_fields_are_archived_not_dropped(project: Path, fake_key: None) -> None:
    """★ C 的桌面端会往 payload 里塞 taskId / taskHistory / connectivityMode
    等字段。引擎现在还不消费它们 —— 但「丢掉」和「存着没用」是两回事：
    丢掉之后就再也无法回答「用户当时到底提交了什么」。"""

    service = _service(project)
    service.command(
        _command(
            "submit_requirement",
            service.run_id,
            project,
            requirement="随便做点什么",
            taskId="task-1234",
            connectivityMode="local",
            taskHistory=[{"taskId": "task-0", "title": "上一个任务"}],
        )
    )
    record = json.loads(next((project / ".codentum" / "requirements").glob("*.json")).read_text("utf-8"))
    assert record["payload"]["taskId"] == "task-1234"
    assert record["payload"]["connectivityMode"] == "local"
    assert record["payload"]["taskHistory"][0]["taskId"] == "task-0"


def test_empty_requirement_is_rejected_with_a_reason(project: Path, fake_key: None) -> None:
    """★ 与其把一个没有需求正文的 packet 建出来再让模型去猜，
    不如在入口就说清楚缺什么 —— 08-10 那份 blocker 报告就是这么来的。"""

    service = _service(project)
    receipt = service.command(
        _command("submit_requirement", service.run_id, project, requirement="   ")
    )
    assert receipt["status"] == "rejected"
    assert receipt["reason"] == "requirement_text_is_empty"
    assert service.packets() == {}


def test_submit_without_key_is_rejected_not_silently_queued(project: Path, no_key: None) -> None:
    """★ 网关本该在能力检查那一关就拦住它。这条测的是「万一没拦住」——
    引擎自己也不能把一个永远不会被执行的 packet 收下。"""

    service = _service(project)
    receipt = service.command(
        _command("submit_requirement", service.run_id, project, requirement="做点什么")
    )
    assert receipt["status"] == "rejected"
    assert receipt["reason"] == "capability_unavailable:requirements"
    assert service.packets() == {}


def test_unimplemented_action_is_rejected_not_silently_applied(project: Path, fake_key: None) -> None:
    """★ 走到这里说明网关的能力检查放行了一个没实现的动作。
    静默回 applied 会让「暂停」看起来生效了。"""

    service = _service(project)
    receipt = service.command(_command("pause_at_safe_point", service.run_id, project))
    assert receipt["status"] == "rejected"
    assert receipt["reason"] == "action_not_implemented:pause_at_safe_point"


def test_admission_runs_before_anything_is_written(project: Path, fake_key: None) -> None:
    """★ 先污染再检查是不行的：违规 packet 一旦落进 packets/，
    下次 load_state 会把它读回来。"""

    service = _service(project)
    receipt = service.command(
        _command(
            "submit_requirement",
            service.run_id,
            project,
            requirement="做点什么",
            # 空的 ownsPaths 会被 check_owns_paths 拒（I1 单写者要求写路径明确）
            ownsPaths=[],
            budgetCny=-1,
        )
    )
    # 空列表会退回默认值 workspace/，所以这条应当通过 —— 断言的是
    # 「默认值本身是合法的」，否则每一次正常提交都会被准入拒掉。
    assert receipt["status"] == "accepted", receipt.get("reason")


# ══════════════════════════════════════════════════════════════
#  停止
# ══════════════════════════════════════════════════════════════


def test_shutdown_stops_accepting_commands(project: Path, fake_key: None) -> None:
    service = _service(project)
    result = service.shutdown()
    assert result["stopped"] is True
    receipt = service.command(
        _command("submit_requirement", service.run_id, project, requirement="太晚了")
    )
    assert receipt["status"] == "rejected"
    assert receipt["reason"] == "engine_stopping"


# ══════════════════════════════════════════════════════════════
#  intake 层
# ══════════════════════════════════════════════════════════════


def test_generated_packet_id_matches_the_frozen_pattern() -> None:
    """★ 契约的 pattern 是 `^wp-[0-9a-z]{6,}$` —— 大写和连字符都会被拒。
    直接用 uuid4() 的字符串形式会踩这个坑（08-10 已经踩过一次）。"""

    import re

    for _ in range(50):
        assert re.fullmatch(r"^wp-[0-9a-z]{6,}$", str(new_packet_id()))


def test_default_acceptance_is_executable_not_a_manual_placeholder() -> None:
    """★ 验收谓词必须是**能被跑一遍**的，不能是 manual 占位。

    2026-08-12 实测：模型只写了规格要求的两个文件中的一个（第 5 轮自己承认了），
    packet 仍被判 accepted —— 因为 `kind: manual` 的谓词永远不会被执行，
    于是「产生了一条真实证据」就等于「验收通过」。

    ★ 为什么默认跑测试：需求是自由文本，机器判不了「做得对不对」；
      但「你自己写的测试跑不跑得过」是机器能判的，
      而且它把举证责任推回给了执行者。
    """

    packet = build_packet_for_requirement(
        packet_id=new_packet_id(),
        requirement="做个东西",
        owns_paths=("workspace/",),
        reads_paths=(),
        model="qwen-coder-plus-1106",
        effort="medium",
        budget_cny=1.0,
        acceptance_author="qa",
    )
    assert packet.acceptance.kind == "test", "manual 谓词永远不会被执行"
    assert packet.acceptance.predicate.split(), "谓词必须是可执行命令"
    # authoredBy 不得等于 packet.role —— 自己给自己定验收即作弊，契约强制
    assert packet.acceptance.authoredBy != packet.role


def test_manual_placeholder_is_still_available_and_says_it_needs_a_human() -> None:
    """★ 保留 manual 那条路：确实无法机器判定时，要如实标成需人工判定，
    而不是塞一条跑不了的假 test 谓词。"""

    packet = build_packet_for_requirement(
        packet_id=new_packet_id(),
        requirement="做个东西",
        owns_paths=("workspace/",),
        reads_paths=(),
        model="qwen-coder-plus-1106",
        effort="medium",
        budget_cny=1.0,
        acceptance_author="qa",
        executable_acceptance=False,
    )
    assert packet.acceptance.kind == "manual"
    assert "人工判定" in packet.acceptance.predicate


def test_acceptance_author_prefers_intake_once_the_rolespec_exists() -> None:
    """★ 这条钉的是「B 补完 RoleSpec 之后行为会自己变对」。

    概念上写占位验收的是 Intake。但 `check_role_exists` 会拒绝任何不在已加载
    RoleSpec 里的 authoredBy，而 intake 正是 B 还没写的 8 份之一。
    现在退到 qa 是权宜，**不是设计**——所以要有一条测试证明它只是权宜：
    intake 一旦存在就必须被选中。

    如果有人把优先级写死成 qa，这条会红。
    """

    assert choose_acceptance_author(["coder", "qa", "reviewer"], packet_role="coder") == "qa"
    assert (
        choose_acceptance_author(["coder", "intake", "qa", "reviewer"], packet_role="coder")
        == "intake"
    )


def test_acceptance_author_never_equals_the_packet_role() -> None:
    """★ 自己给自己定验收即作弊（I2），契约层的 check_self_review 会拒。
    这里在生成侧就堵住，避免每次提交都走到准入才被打回。"""

    assert choose_acceptance_author(["coder", "qa"], packet_role="qa") == "coder"
    with pytest.raises(ValueError, match="没有任何可用于署名验收的角色"):
        choose_acceptance_author(["coder"], packet_role="coder")


def test_state_dir_is_coherent_from_the_very_first_launch(project: Path, fake_key: None) -> None:
    """★ 引擎一启动，`.codentum/` 就必须是一份**完整的空状态**。

    2026-08-11 实机撞到的回归：`EngineSession` 为了放 engine-session.json
    先把 `.codentum/` 建了出来，但完整形状要等第一次 `save_state()` 才铺。
    于是用户打开项目、还没提交任何需求时，桌面端读到一个残缺目录，
    界面上排开五条 `[missing] Required state file is missing: ...`。

    ★ 这比「目录根本不存在」更糟 —— 不存在时桌面端显示「尚未初始化」，
      残缺时它显示的是一串错误。**半个状态目录比没有状态目录更坏。**

    形状以 `fixtures/golden-state/empty` 为准。
    """

    _service(project)  # 只构造，不发任何命令

    state = project / ".codentum"
    for member in ("graph.json", "budget.json", "decisions.jsonl"):
        assert (state / member).is_file(), f"缺 {member}，桌面端会报 [missing]"
    for directory in ("packets", "evidence", "knowledge"):
        assert (state / directory).is_dir(), f"缺 {directory}/，桌面端会报 [missing]"

    # ★ graph.json 必须是合法的空图，不能是空文件 —— 桌面端要解析它
    graph = json.loads((state / "graph.json").read_text("utf-8"))
    assert graph["dependency"]["nodes"] == []
    assert graph["ownership"]["locks"] == []


def test_ensure_state_dir_never_clobbers_existing_state(project: Path, fake_key: None) -> None:
    """★ 只补缺的，已存在的一律不动。

    否则引擎重启会把上一轮的 graph.json 覆盖成空图 —— 那是把「恢复」
    变成「清空」，比不铺形状严重得多。
    """

    service = _service(project)
    service.command(
        _command("submit_requirement", service.run_id, project, requirement="做个东西")
    )
    graph_before = (project / ".codentum" / "graph.json").read_text("utf-8")
    packets_before = sorted(p.name for p in (project / ".codentum" / "packets").glob("*.json"))
    assert packets_before, "前置条件没满足：没有 packet 落盘"

    _service(project)  # 模拟重启

    assert (project / ".codentum" / "graph.json").read_text("utf-8") == graph_before
    assert sorted(
        p.name for p in (project / ".codentum" / "packets").glob("*.json")
    ) == packets_before


def test_role_specs_are_projected_into_the_project(project: Path, fake_key: None) -> None:
    """★ 引擎启动就把 B 的 RoleSpec 投影进 `<project>/.codentum/roles/`。

    桌面端「研发团队」页读的是**项目内**的 `roles/`，而真源在
    `packages/roles/specs/`。两者之间原本没有任何人搬运 ——
    于是界面显示「系统岗位 11、项目投影 0」：名字对得上，
    但**不代表这 11 个角色真的被系统加载了**。

    这个搬运归装配点：它是唯一同时知道「RoleSpec 从哪来」
    和「状态目录在哪」的地方。
    """

    service = _service(project)
    roles_dir = project / ".codentum" / "roles"
    projected = sorted(p.stem for p in roles_dir.glob("*.json"))

    assert projected, "roles/ 是空的，桌面端会显示「项目投影 0」"
    assert projected == sorted(str(spec.id) for spec in service._role_specs)
    assert len(projected) >= 11, f"只投影了 {len(projected)} 份，B 已经补齐 11 个角色"


def test_role_skills_are_projected_into_project_shared_space(
    project: Path,
    fake_key: None,
) -> None:
    """★ RoleSpec 只说明“要用哪个 Skill”，共享空间才是 Worker 能读的正文副本。

    C 和 Worker 都不该依赖 `packages/roles/skills/` 这个源码目录。引擎启动时
    投影到 `.codentum/skills/shared/`，才算进入项目级共享空间。
    """

    _service(project)
    shared_dir = project / ".codentum" / "skills" / "shared"
    projected = sorted(p.name for p in shared_dir.iterdir() if p.is_dir())

    assert projected == [
        "architecture",
        "backend",
        "cost-governance",
        "debugging",
        "delivery",
        "evolution",
        "frontend",
        "integration",
        "planning",
        "requirements",
        "review",
        "security",
        "testing",
    ]
    assert "# Architecture Skill" in (shared_dir / "architecture" / "SKILL.md").read_text("utf-8")
    assert "# Backend Skill" in (shared_dir / "backend" / "SKILL.md").read_text("utf-8")
    assert "# Cost Governance Skill" in (shared_dir / "cost-governance" / "SKILL.md").read_text("utf-8")
    assert "# Delivery Skill" in (shared_dir / "delivery" / "SKILL.md").read_text("utf-8")
    assert "# Debugging Skill" in (shared_dir / "debugging" / "SKILL.md").read_text("utf-8")
    assert "# Evolution Skill" in (shared_dir / "evolution" / "SKILL.md").read_text("utf-8")
    assert "# Frontend Skill" in (shared_dir / "frontend" / "SKILL.md").read_text("utf-8")
    assert "# Integration Skill" in (shared_dir / "integration" / "SKILL.md").read_text("utf-8")
    assert "# Planning Skill" in (shared_dir / "planning" / "SKILL.md").read_text("utf-8")
    assert "# Requirements Skill" in (shared_dir / "requirements" / "SKILL.md").read_text("utf-8")
    assert "# Review Skill" in (shared_dir / "review" / "SKILL.md").read_text("utf-8")
    assert "# Security Skill" in (shared_dir / "security" / "SKILL.md").read_text("utf-8")
    assert "# Testing Skill" in (shared_dir / "testing" / "SKILL.md").read_text("utf-8")


def test_mcp_services_are_projected_into_project_state(
    project: Path,
    fake_key: None,
) -> None:
    _service(project)
    mcp_dir = project / ".codentum" / "mcp"
    projected = sorted(p.name for p in mcp_dir.glob("*.json"))

    # ★ 守「四个基础服务都被投影」而非「恰好只有这四个」——
    #   第三方应用会随需求增删，写死清单会让每次加应用都要改测试。
    assert {"agentteams.json", "browser.json", "filesystem.json", "git.json"} <= set(projected)
    filesystem = json.loads((mcp_dir / "filesystem.json").read_text("utf-8"))
    agentteams = json.loads((mcp_dir / "agentteams.json").read_text("utf-8"))
    # ★ 这两条原先断言的是 status == "connected" 和一份写死的工具清单。
    #   而 filesystem.json 当时**根本没有 command** —— 不可能连上任何东西。
    #   也就是说测试把「声称已连接但无法启动」钉死了：去修正它反而会变红。
    #
    #   同样的断言在 packages/roles/tests/test_loader.py 里还有一份，
    #   两处一起把这个谎锁住了。★ 断言的对象选错了：
    #   该断言的不是「这个字段等于这个值」，是「**这个字段没有说谎**」。
    assert filesystem["status"] == "disconnected", "没启用的 server 不得声称已连接"
    assert filesystem["tools"] == [], "可启动的 server 不该预先罗列工具 —— 会随版本漂移变成谎报"
    assert agentteams["status"] == "disconnected"
    assert agentteams["authentication"] == "missing"
    assert "error" in agentteams


def test_projection_matches_the_source_spec_field_for_field(
    project: Path, fake_key: None
) -> None:
    """★ 投影必须与 B 的源文件逐字段一致 —— 它是副本，不是二次加工。

    只断言「文件存在」是不够的：写出一份**结构合法但内容不对**的副本，
    桌面端照样能读，只是显示的是假事实。
    """

    _service(project)
    source_dir = Path(__file__).resolve().parents[3] / "packages" / "roles" / "specs"
    for source in sorted(source_dir.glob("*.json")):
        expected = json.loads(source.read_text("utf-8"))
        actual = json.loads((project / ".codentum" / "roles" / source.name).read_text("utf-8"))
        assert actual == expected, f"{source.name} 的投影与源文件不一致"


def test_projection_is_refreshed_not_merely_created(project: Path, fake_key: None) -> None:
    """★ 与 `ensure_state_dir()` 的「只补缺不覆盖」相反：投影必须每次重写。

    投影的语义是「真源的副本」。留着旧副本比缺副本更糟 —— B 改了 RoleSpec
    而项目里还是上一版时，桌面端会显示一份**看起来正确的过时事实**，
    而没有任何东西会报错。
    """

    _service(project)
    stale = project / ".codentum" / "roles" / "coder.json"
    stale.write_text('{"id": "coder", "usesModel": false}', encoding="utf-8")

    _service(project)  # 重启

    refreshed = json.loads(stale.read_text("utf-8"))
    assert refreshed["usesModel"] is True, "旧副本没有被刷新，桌面端会显示过时的角色定义"


def test_sedimented_memory_is_retrieved_without_any_knowledge_sources(
    project: Path,
    fake_key: None,
) -> None:
    """★ 进化层攒的经验，不该以「用户这次顺便传了几篇文档」为条件才被读到。

    原先检索是嵌在 `if sources:` 里的 —— 没有 resourceSelections 的执行，
    记忆一次都不会被读。这个缺陷是**静默**的：
    写入侧一直在攒 L0/L1，读取侧一次没读过，
    从外面看只是「记忆系统在跑，好像没起作用」。

    这条测试就是那个静默缺陷的声音。
    """

    from codentum_contracts import MemoryEntry
    from codentum_contracts.interfaces import MemoryScope
    from codentum_harness.memory_index import PersistentMemoryIndex

    service = _service(project)
    # ★ 注意：**没有** resourceSelections
    service.command(_command("submit_requirement", service.run_id, project, requirement="实现订阅费用页面"))
    packet_id = next(iter(service.packets()))
    role_spec = service._role_specs[0]

    # 模拟上一个 packet 沉淀下来的 L1 经验（role 作用域）
    index = PersistentMemoryIndex(project / ".codentum" / "memory" / "index")
    index.write_now(
        MemoryEntry(
            ref="",
            level="L1",
            scope=MemoryScope(kind="role", role=role_spec.id),
            text="工具 run_tests 失败：ImportError: No module named 'src'",
            created_at="2026-08-14T10:00:00Z",
        )
    )

    class _Request:
        pass

    request = _Request()
    request.packet_id = packet_id  # type: ignore[attr-defined]

    memory_candidates = [
        c for c in service._context_loader(request, role_spec) if c.ref.startswith("memory:")
    ]
    assert memory_candidates, "没有知识资源时，沉淀下来的经验一条都没被读到"
    assert any("ImportError" in c.text for c in memory_candidates)
    projection = json.loads(
        (project / ".codentum" / "memory" / "projection.json").read_text(encoding="utf-8")
    )
    assert projection["sourceCount"] == 0
    assert projection["retrievalCount"] >= 1
    assert any(hit["category"] == "experience" for hit in projection["retrievals"])


# ══════════════════════════════════════════════════════════════
#  MCP 接线
#
#  ★ 在这组测试之前，`mcp_config_dir` **全仓库无人传** —— 定义了、
#    文档写了、真机演示过，但在生产路径上根本没有办法把它打开。
#    这与「langgraph 装了零 import」是同一个形状，而且同样
#    **没有任何测试会红**：mcp_client 那 15 条测试全是绿的，
#    它们测的是模块本身，模块本身当然是对的。
#
#  ★ 所以这组守的不是「MCP 能不能用」，是「它到底有没有被接上」。
# ══════════════════════════════════════════════════════════════


def _mcp_config_dir(tmp_path: Path) -> Path:
    """铺一个**真的能连上**的 MCP 配置目录（子进程跑最小 JSON-RPC server）。"""

    import sys

    from test_mcp_client import _FAKE_SERVER  # 复用那边的真实假 server

    script = tmp_path / "fake_mcp_server.py"
    script.write_text(_FAKE_SERVER, encoding="utf-8")

    config_dir = tmp_path / "mcp"
    config_dir.mkdir()
    (config_dir / "github.json").write_text(
        json.dumps({
            "schemaVersion": 1, "id": "github", "name": "GitHub",
            "transport": "stdio", "command": sys.executable, "args": [str(script)],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    # ★ 再放一个**关闭**的配置：仓库里真实的 mcp/ 目录就是出厂即关闭的，
    #   夹具必须复现这一点，否则「全部被跳过」这条路径永远测不到。
    (config_dir / "notion.json").write_text(
        json.dumps({
            "schemaVersion": 1, "id": "notion", "name": "Notion",
            "transport": "stdio", "command": "npx", "args": ["-y", "notion-mcp"],
            "enabled": False, "credentialHowTo": "去 Notion 建 integration",
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    return config_dir


def test_mcp_is_off_when_not_configured(project: Path) -> None:
    """★ 不配 = 不连。而且不该留下任何痕迹让人误以为连过。"""

    service = _service(project)
    assert service._ensure_mcp() is None
    # ★ 断言的是**报告文件**不存在，不是目录 ——  属于
    #   状态目录的标准布局，ensure_state_dir() 本来就会建它。
    #   拿目录存在与否当判据，测的是别人的行为，不是这里的。
    assert not (project / ".codentum" / "mcp" / "connections.json").exists()


def test_mcp_connects_once_and_is_shared(project: Path, tmp_path: Path) -> None:
    """★ 「主 Agent 接一次」必须是**结构性**的，不是靠自觉。

    原先 `_AgentRun.__init__` 每个 packet 连一遍 —— 8 路并行就是
    48 个 npx 进程。现在 runner 只收已连好的工具箱，
    **它已经不知道该怎么连了**，这个错误不可能再犯。

    这条测同一个 service 反复要工具箱时拿到的是**同一个实例**。
    """

    service = _service(project, mcp_config_dir=_mcp_config_dir(tmp_path))
    try:
        first = service._ensure_mcp()
        assert first is not None
        assert first is service._ensure_mcp() is service._ensure_mcp(), "每次都重连了"
        assert any("github__" in s.name for s in first.schemas()), "工具没进工具面"
    finally:
        if service._mcp is not None:
            service._mcp.close()


def test_mcp_connection_report_lands_on_disk(project: Path, tmp_path: Path) -> None:
    """★ 没有这份报告，三种情况从外面看是同一个样子：

    「没配置」「配了但一个都没连上」「连上了但模型没调用」——
    都表现为「MCP 好像没起作用」。而它们的处理办法完全不同。
    """

    service = _service(project, mcp_config_dir=_mcp_config_dir(tmp_path))
    try:
        service._ensure_mcp()
        report = json.loads(
            (project / ".codentum" / "mcp" / "connections.json").read_text(encoding="utf-8")
        )
        assert report["toolCount"] >= 1
        (server,) = report["servers"]
        assert server["id"] == "github"
        assert server["connected"] is True

        # ★ 被跳过的条目必须连**原因**一起列出来。
        #   否则「目录写错了」「配置全是关的」「连上了但模型没调用」
        #   三种情况在报告里长得一模一样 —— 而它们的解法完全不同。
        #   仓库里那 10 个配置全部处于「跳过」状态，一个刚接手的人
        #   把目录配对了却什么都没发生，会先去怀疑代码。
        (skipped,) = report["skipped"]
        assert skipped["file"] == "notion.json"
        assert "enabled=false" in skipped["reason"]
        assert "去 Notion 建 integration" in skipped["reason"], "怎么打开的指引必须带出来"
    finally:
        if service._mcp is not None:
            service._mcp.close()


def test_runner_config_cannot_connect_mcp_by_itself() -> None:
    """★ 「每个 packet 连一次」在结构上必须不可表达。

    收目录 → runner 有能力自己连 → 迟早有人在 __init__ 里连。
    收工具箱 → runner 根本没有连接的手段。

    这是本项目那条约束实现优先级的直接应用：
    **不可见 > 无权限 > 被拦截 > 提示词劝阻**。
    一条「请只连一次」的注释属于最后一档。
    """

    from codentum_engine.agent_runner import AgentRunnerConfig

    fields = {f.name for f in dataclasses.fields(AgentRunnerConfig)}
    assert "mcp_toolbox" in fields
    assert "mcp_config_dir" not in fields, "收目录就等于把「自己连」这条路留着"


def test_judgement_hits_are_recorded_to_disk(project: Path, fake_key: None) -> None:
    """★ 影子期不落盘 = 晋级永远无据可依。

    晋级到 enforcing 的第一个条件是「在真实案例上命中过 ≥1 次」。
    没有这份账本，那个条件**永远无法被满足** ——
    影子判据会永远停在影子里，那和不加这条判据是一样的。

    ★ 同时守「没命中的也要记」：资产负债表要靠它区分
      「跑过 N 次一次没命中」（可能多余）与「根本没人记录」（观测坏了）。
      只记命中的话，两者在账本上长得一模一样。
    """

    service = _service(project)
    service.command(_command("submit_requirement", service.run_id, project, requirement="实现登录页"))

    ledger = project / ".codentum" / "judgements" / "hits.jsonl"
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line]
    assert rows, "准入跑过了，却没有留下任何判据评估记录"

    by_rule = {row["rule"] for row in rows}
    assert "check_budget_limit" in by_rule, "没命中的规则也该留下运行记录"

    shadow = [row for row in rows if row["mode"] == "shadow"]
    assert shadow, "影子判据一条记录都没有 —— 影子期攒不到任何证据"


# ══════════════════════════════════════════════════════════════
#  项目初始化：打开一个新文件夹就提需求
#
#  ★ 上面那个 `project` 夹具是 `git init` **不带提交** —— 也就是说
#    本文件此前那 33 条测试，一直跑在一个 **worker 根本起不来**的项目上，
#    却全部是绿的。它们测的是准入与状态，碰不到 worktree 那一层。
#
#    真实现象（2026-08-15 实测）：
#      spawn 失败，packet wp-xxx 保持 ready 并释放锁：
#      WorktreeIsolationError: fatal: invalid reference: HEAD
#
#    后果不是崩溃 —— 是 packet 永远停在 ready，使用者只看到「什么都没发生」。
# ══════════════════════════════════════════════════════════════


def test_engine_startup_makes_a_fresh_repo_worktree_ready(project: Path, fake_key: None) -> None:
    """★ 引擎起来之后，隔离层必须**真的能用** —— 不只是「初始化函数被调过」。

    断言落在 `GitWorktreeManager.create()` 能成功，而不是「HEAD 存在」：
    后者是前者的必要条件，测前者才是测这件事本身。
    """

    from codentum_harness.worker import GitWorktreeManager

    _service(project)  # 构造即初始化

    workspace = project.parent / "codentum-workers" / "probe"
    assert GitWorktreeManager(project).create(workspace).exists()


def test_engine_startup_never_rewrites_a_project_that_has_history(
    tmp_path: Path, fake_key: None
) -> None:
    """★ 安全判据：使用者把 Codentum 指向一个真实项目时，
    它绝不能往那段历史里写东西。
    """

    repo = tmp_path / "real-project"
    repo.mkdir()
    for args in (
        ["init", "-q"], ["config", "user.name", "t"], ["config", "user.email", "t@e.com"],
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    (repo / "a.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "real work"], cwd=repo, check=True, capture_output=True)

    def head_log() -> str:
        return subprocess.run(
            ["git", "log", "--oneline"], cwd=repo, check=True, capture_output=True, text=True
        ).stdout

    before = head_log()
    _service(repo)
    assert head_log() == before, "引擎在使用者已有的仓库历史上加了提交"


def test_plain_folder_without_git_is_initialized(tmp_path: Path, fake_key: None) -> None:
    """连 `.git` 都没有的普通目录 —— 桌面端「打开一个新文件夹」的最常见形态。"""

    plain = tmp_path / "plain"
    plain.mkdir()

    service = _service(plain)

    assert (plain / ".git").exists()
    assert service._project_init is not None
    assert service._project_init.changed is True


def test_scheduling_and_flow_projections_land_during_a_real_run(
    project: Path, fake_key: None
) -> None:
    """★ 缺口 ③ 的接线判据：C 的读取与显示早就写好了，卡在没有权威数据源。

    这条守的不是「投影算得对」（那在 test_projections.py），
    而是**它到底有没有在真实运行里被写出来**。
    """

    service = _service(project)
    service.command(_command("submit_requirement", service.run_id, project, requirement="做一个待办清单"))
    for thread in service._workers:
        thread.join(timeout=60)

    state_dir = project / ".codentum"
    scheduling = json.loads((state_dir / "scheduling.json").read_text("utf-8"))
    flow = json.loads((state_dir / "flow.json").read_text("utf-8"))

    assert scheduling["schemaVersion"] == 1
    # ★ WIP 上限必须是**真正被执行的**那个（控制平面的 wip_limiter 默认值），
    #   而不是投影自己编的数字。
    assert scheduling["wipLimits"] == {"running": 4, "review": 2}
    # ★ readyQueue 必须是字符串数组 —— 桌面端守卫 fail-closed，
    #   形状不对会让整个文件被拒
    assert all(isinstance(x, str) for x in scheduling["readyQueue"])
    assert flow["schemaVersion"] == 1

    # 决策日志必须有内容，否则 flow 里的时长全部来自空气
    decisions = (state_dir / "decisions.jsonl").read_text("utf-8").strip()
    assert decisions, "转移发生过，decisions.jsonl 却是空的 —— flow 无据可算"


def test_engine_wires_the_result_integrator(project: Path, fake_key: None) -> None:
    """★ 缺口 ⑥ 的接线判据：合入器必须真的被注入。

    没注入的话，packet 会是 accepted 而项目里什么都没有 ——
    而那正是这条缺口原本的样子：**「验收通过」只是一句状态字符串**。

    这条守的是「接上了」；合入本身的行为判据在
    packages/harness/tests/test_integrate.py（7 条）。
    """

    loop = _service(project)._build_loop()
    assert loop.result_integrator is not None, "合入器没被注入 —— accepted 不代表东西进了项目"
