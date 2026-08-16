from __future__ import annotations

import json
from pathlib import Path

import pytest
from codentum_contracts import BudgetGrantRuntime, ModelRouting, PacketId, RoleId, RoleSpec, SpawnRequest
from codentum_contracts.state import RoleSkill
from codentum_harness.context_broker import ContextCandidate, assemble_context_bundle
from codentum_harness.prompt_bundle import (
    PromptBundleError,
    assemble_worker_prompt_bundle,
    load_worker_prompt_bundle,
    write_worker_prompt_bundle,
)
from codentum_roles import default_specs_dir, load_role_spec_file


def request(workspace: Path, *, role: RoleId = "reviewer") -> SpawnRequest:
    return SpawnRequest(
        packet_id=PacketId("wp-abcdef"),
        role=role,
        mounts=(),
        tools=("read_file", "write_review"),
        routing=ModelRouting(model="qwen-max", effort="high"),
        budget=BudgetGrantRuntime(limit_cny=1.0, degradation_chain=("summary", "reference")),
        workspace=str(workspace),
        attempt=1,
    )


def role_spec(*, role: RoleId = "reviewer") -> RoleSpec:
    return RoleSpec(
        id=role,
        summary="review visible evidence",
        usesModel=True,
        writes=("evidence/reviews/**",),
        reads=("packages/contracts/**", "evidence/diffs/**"),
        invisible=("evidence/coder-private/**",),
        tools=("read_file", "write_review"),
        transitions=(),
    )


def test_prompt_bundle_is_stable_and_writes_manifest(tmp_path: Path) -> None:
    req = request(tmp_path / "worker")
    context = assemble_context_bundle(
        role_spec(),
        candidates=(
            ContextCandidate(
                ref="diff",
                artifact_path="evidence/diffs/wp-abcdef.patch",
                text="visible diff",
                required=True,
            ),
        ),
        char_budget=100,
    )

    first = write_worker_prompt_bundle(
        request=req,
        role_spec=role_spec(),
        context=context,
        evidence_dir=tmp_path / "evidence-a",
    )
    second = write_worker_prompt_bundle(
        request=req,
        role_spec=role_spec(),
        context=context,
        evidence_dir=tmp_path / "evidence-b",
    )

    assert first == second
    manifest = json.loads(
        (tmp_path / "evidence-a" / "prompt" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["digest"] == first.digest
    assert manifest["context_refs"] == ["diff"]
    assert (tmp_path / "evidence-a" / "prompt" / "system.md").read_text(encoding="utf-8") == first.system
    assert (tmp_path / "evidence-a" / "prompt" / "user.md").read_text(encoding="utf-8") == first.user
    assert load_worker_prompt_bundle(tmp_path / "evidence-a") == first


def test_prompt_bundle_never_leaks_denied_context_text(tmp_path: Path) -> None:
    context = assemble_context_bundle(
        role_spec(),
        candidates=(
            ContextCandidate(
                ref="diff",
                artifact_path="evidence/diffs/wp-abcdef.patch",
                text="visible diff",
                priority=1,
            ),
            ContextCandidate(
                ref="coder-private",
                artifact_path="evidence/coder-private/wp-abcdef/reasoning.md",
                text="secret private reasoning",
                priority=2,
            ),
        ),
        char_budget=100,
    )

    bundle = assemble_worker_prompt_bundle(request(tmp_path / "worker"), role_spec(), context=context)

    assert "visible diff" in bundle.user
    assert "coder-private" in bundle.user
    assert "secret private reasoning" not in bundle.user


def test_prompt_bundle_rejects_role_mismatch(tmp_path: Path) -> None:
    with pytest.raises(PromptBundleError, match="does not match"):
        assemble_worker_prompt_bundle(request(tmp_path / "worker", role="coder"), role_spec())


def test_prompt_bundle_can_be_converted_to_model_request(tmp_path: Path) -> None:
    bundle = assemble_worker_prompt_bundle(request(tmp_path / "worker"), role_spec())

    model_request = bundle.to_model_request(effort="high")

    assert model_request.system == bundle.system
    assert model_request.messages[0].content == bundle.user
    assert model_request.effort == "high"


def test_prompt_bundle_includes_rolespec_prompt_ref(tmp_path: Path) -> None:
    coder_spec = load_role_spec_file(default_specs_dir() / "coder.json")

    bundle = assemble_worker_prompt_bundle(request(tmp_path / "worker", role="coder"), coder_spec)

    assert "## Role Prompt" in bundle.system
    assert "Coder Prompt" in bundle.system


def test_prompt_bundle_includes_active_role_skills(tmp_path: Path) -> None:
    coder_spec = load_role_spec_file(default_specs_dir() / "coder.json")

    bundle = write_worker_prompt_bundle(
        request=request(tmp_path / "worker", role="coder"),
        role_spec=coder_spec,
        evidence_dir=tmp_path / "evidence",
    )

    assert "## Active Skills" in bundle.system
    assert "### frontend" in bundle.system
    assert "# Frontend Skill" in bundle.system
    assert "### backend" in bundle.system
    assert "# Backend Skill" in bundle.system
    assert "### testing" in bundle.system
    assert "# Testing Skill" in bundle.system
    assert "### debugging" in bundle.system
    assert "# Debugging Skill" in bundle.system

    manifest = json.loads(
        (tmp_path / "evidence" / "prompt" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["skill_refs"] == ["frontend", "backend", "testing", "debugging"]


def test_prompt_bundle_can_read_active_skills_from_project_shared_space(tmp_path: Path) -> None:
    shared_dir = tmp_path / ".codentum" / "skills" / "shared"
    _write_shared_skill(shared_dir, "frontend", "# Shared Frontend Skill\n\nUse project rules.")
    spec = RoleSpec(
        id="coder",
        summary="write code",
        usesModel=True,
        writes=("workspace/**",),
        reads=("packages/contracts/**",),
        tools=("read_file", "write_file"),
        transitions=(),
        skills=(RoleSkill(id="frontend", scope="role", state="active"),),
    )

    bundle = write_worker_prompt_bundle(
        request=request(tmp_path / "worker", role="coder"),
        role_spec=spec,
        evidence_dir=tmp_path / "evidence",
        skills_dir=shared_dir,
    )

    assert "# Shared Frontend Skill" in bundle.system
    assert "# Frontend Skill" not in bundle.system
    manifest = json.loads(
        (tmp_path / "evidence" / "prompt" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["skill_refs"] == ["frontend"]
    assert manifest["skill_source"] == "project_shared"


def test_prompt_bundle_excludes_inactive_role_skills(tmp_path: Path) -> None:
    spec = RoleSpec(
        id="coder",
        summary="write code",
        usesModel=True,
        writes=("workspace/**",),
        reads=("packages/contracts/**",),
        tools=("read_file", "write_file"),
        transitions=(),
        skills=(
            # ★ 用契约类型而不是裸 dict：RoleSpec.skills 声明的是
            #   tuple[RoleSkill, ...]，裸 dict 运行时能过、mypy 会拦。
            RoleSkill(id="frontend", scope="role", state="candidate"),
            RoleSkill(id="testing", scope="role", state="active"),
        ),
    )

    bundle = assemble_worker_prompt_bundle(request(tmp_path / "worker", role="coder"), spec)

    assert "### testing" in bundle.system
    assert "# Testing Skill" in bundle.system
    assert "### frontend" not in bundle.system
    assert "# Frontend Skill" not in bundle.system


def test_prompt_bundle_loader_rejects_digest_mismatch(tmp_path: Path) -> None:
    write_worker_prompt_bundle(
        request=request(tmp_path / "worker"),
        role_spec=role_spec(),
        evidence_dir=tmp_path / "evidence",
    )
    (tmp_path / "evidence" / "prompt" / "user.md").write_text("tampered\n", encoding="utf-8")

    with pytest.raises(PromptBundleError, match="digest mismatch"):
        load_worker_prompt_bundle(tmp_path / "evidence")


def _write_shared_skill(root: Path, skill_id: str, body: str) -> None:
    skill_dir = root / skill_id
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "manifest.json").write_text(
        json.dumps({"id": skill_id, "version": "0.0.0"}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text(body + "\n", encoding="utf-8")


# ══════════════════════════════════════════════════════════════
#  使用者在界面上追加的说明
# ══════════════════════════════════════════════════════════════


def test_operator_notes_reach_the_system_prompt(tmp_path: Path) -> None:
    """★ 这是「界面填的提示词真的生效」的可执行形态。

    在此之前 systemPrompt 存在桌面端、加密落盘、界面显示已保存，
    而引擎从不读它 —— 与那把从没被解密过的 API Key 是同一种缺陷。
    """

    bundle = assemble_worker_prompt_bundle(
        request(tmp_path),
        role_spec(),
        operator_notes=(("全局", "所有代码写中文注释。"), ("Agent reviewer", "先看边界条件。")),
    )

    assert "所有代码写中文注释。" in bundle.system
    assert "先看边界条件。" in bundle.system


def test_each_note_says_where_it_came_from(tmp_path: Path) -> None:
    """★ 一段来源不明的提示词，出问题时无法归因。

    而这是**唯一可以被使用者随时改动**的内容，也就是最需要能归因的那段。
    """

    bundle = assemble_worker_prompt_bundle(
        request(tmp_path), role_spec(), operator_notes=(("全局", "X"),)
    )
    assert "### 来自：全局" in bundle.system


def test_notes_come_after_the_hard_constraint_statement(tmp_path: Path) -> None:
    """★ 位置本身在说明它管不着硬约束。

    追加说明排在「Hard constraints live in RoleSpec…」之后，
    而且那一段会再重申一次「不授予任何权限」——
    否则「在提示词里写『你可以改任何文件』」看起来像条能生效的指令。
    """

    bundle = assemble_worker_prompt_bundle(
        request(tmp_path), role_spec(), operator_notes=(("全局", "X"),)
    )
    assert bundle.system.index("Hard constraints live in RoleSpec") < bundle.system.index("Operator Notes")
    assert "不授予任何权限" in bundle.system


def test_no_notes_adds_no_section(tmp_path: Path) -> None:
    """没有追加说明时，提示词与从前**逐字相同**。

    ★ 这条守的是「这次改动不影响没配过的项目」——
      一个空的 `## Operator Notes` 小节会让模型看到一段没有内容的指令。
    """

    with_none = assemble_worker_prompt_bundle(request(tmp_path), role_spec())
    assert "Operator Notes" not in with_none.system


def test_notes_change_the_digest(tmp_path: Path) -> None:
    """★ 提示词变了，摘要必须跟着变。

    摘要不变的话，「这次跑用的是哪版提示词」在证据里不可区分 ——
    而使用者随时可能改它，这正是最需要能分辨的一处。
    """

    base = assemble_worker_prompt_bundle(request(tmp_path), role_spec())
    noted = assemble_worker_prompt_bundle(
        request(tmp_path), role_spec(), operator_notes=(("全局", "X"),)
    )
    assert base.digest != noted.digest


def test_manifest_records_which_scopes_contributed(tmp_path: Path) -> None:
    """★ system.md 很长；manifest 让「这次用了谁的追加说明」一眼可见。"""

    evidence = tmp_path / "ev"
    write_worker_prompt_bundle(
        request=request(tmp_path),
        role_spec=role_spec(),
        evidence_dir=evidence,
        operator_notes=(("全局", "X"), ("Agent reviewer", "Y")),
    )
    manifest = json.loads((evidence / "prompt" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["operator_note_scopes"] == ["全局", "Agent reviewer"]
