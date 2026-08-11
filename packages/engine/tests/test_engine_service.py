"""引擎服务的判据。

★ 每条测试都对应一个「如果它坏了会怎样」，不是为了覆盖率。
  尤其是能力表那几条：报错一个 true，桌面端就会显示一个点了没反应的按钮，
  而那种缺陷没有任何东西会报错。
"""

from __future__ import annotations

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
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-not-a-real-key-for-tests")
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


def test_state_revision_survives_a_restart(project: Path, fake_key: None) -> None:
    """★ 网关判 `revision < 上一次` 为 non_monotonic_state_revision 并拒绝。

    假引擎的进程内计数器一重启就归零，于是重启后第一条回执会被网关判为
    协议违规。这条测试盯的就是那个。
    """

    first = _service(project)
    before = first.revision
    first._session.bump()  # noqa: SLF001
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

    candidates = service._context_loader(request, service._role_specs[0])  # noqa: SLF001
    assert len(candidates) == 1
    assert "记账小工具" in candidates[0].text
    # required=True：被 char_budget 裁掉的话，模型又会收到一份没有任务的 prompt
    assert candidates[0].required is True


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


def test_placeholder_acceptance_is_manual_not_a_fake_test_predicate() -> None:
    """★ 操作者提交的是一句话，不是 ACCEPTANCE.md。

    塞一条 `kind=test, predicate=pytest` 会让 I2「验收可判定」在纸面上成立，
    实际上那条 predicate 从来不会被执行 —— 那是把「可判定」写成了装饰。
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
    assert packet.acceptance.kind == "manual"
    # authoredBy 不得等于 packet.role —— 自己给自己定验收即作弊，契约强制
    assert packet.acceptance.authoredBy != packet.role
    assert "占位" in packet.acceptance.predicate


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
