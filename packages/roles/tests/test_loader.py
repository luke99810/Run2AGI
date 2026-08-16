from __future__ import annotations

import json
from pathlib import Path

import pytest
from codentum_roles import (
    RolePromptLoadError,
    RoleSkillLoadError,
    RoleSpecLoadError,
    load_builtin_role_specs,
    load_builtin_mcp_services,
    load_role_prompt,
    load_role_skill_prompt,
    load_role_spec_file,
    load_role_specs_dir,
    project_mcp_services,
    project_role_skills,
)


def test_builtin_rolespecs_load() -> None:
    specs = load_builtin_role_specs()
    assert {spec.id for spec in specs} == {
        "intake",
        "architect",
        "planner",
        "qa",
        "coder",
        "helper",
        "reviewer",
        "integrator",
        "manager",
        "evolver",
        "guardian",
    }


def test_builtin_rolespecs_have_readable_prompt_refs() -> None:
    for spec in load_builtin_role_specs():
        assert spec.promptRef is not None
        prompt = load_role_prompt(spec)
        assert prompt is not None
        assert prompt.startswith("# ")


def test_builtin_rolespecs_reference_existing_skills() -> None:
    specs = load_builtin_role_specs()
    skills_by_role = {spec.id: tuple(skill.id for skill in spec.skills or ()) for spec in specs}

    assert skills_by_role["intake"] == ("requirements",)
    assert skills_by_role["architect"] == ("architecture", "security")
    assert skills_by_role["planner"] == ("planning", "cost-governance")
    assert skills_by_role["coder"] == ("frontend", "backend", "testing", "debugging")
    assert skills_by_role["helper"] == ("frontend", "backend", "testing", "debugging")
    assert skills_by_role["qa"] == ("testing",)
    assert skills_by_role["reviewer"] == ("review", "security")
    assert skills_by_role["integrator"] == ("integration", "delivery", "testing", "review", "debugging")
    assert skills_by_role["manager"] == ("planning", "cost-governance")
    assert skills_by_role["evolver"] == ("evolution", "review")
    assert skills_by_role["guardian"] == ("review", "security")


def test_builtin_role_skills_have_readable_prompt_body() -> None:
    assert "# Requirements Skill" in load_role_skill_prompt("requirements")
    assert "# Architecture Skill" in load_role_skill_prompt("architecture")
    assert "# Planning Skill" in load_role_skill_prompt("planning")
    assert "# Frontend Skill" in load_role_skill_prompt("frontend")
    assert "# Backend Skill" in load_role_skill_prompt("backend")
    assert "# Testing Skill" in load_role_skill_prompt("testing")
    assert "# Debugging Skill" in load_role_skill_prompt("debugging")
    assert "# Delivery Skill" in load_role_skill_prompt("delivery")
    assert "# Review Skill" in load_role_skill_prompt("review")
    assert "# Security Skill" in load_role_skill_prompt("security")
    assert "# Integration Skill" in load_role_skill_prompt("integration")
    assert "# Cost Governance Skill" in load_role_skill_prompt("cost-governance")
    assert "# Evolution Skill" in load_role_skill_prompt("evolution")


def test_builtin_skill_manifests_cover_rolespec_bindings() -> None:
    specs = load_builtin_role_specs()
    skills_dir = Path(__file__).resolve().parents[1] / "skills"

    for spec in specs:
        for skill in spec.skills or ():
            manifest = json.loads((skills_dir / skill.id / "manifest.json").read_text(encoding="utf-8"))
            assert spec.id in manifest["appliesTo"]


def test_builtin_mcp_services_load_and_say_connection_truth() -> None:
    """★ 这条测试原先自己就在锁一个谎。

    它的名字是 "say connection truth"，内容却是：

        assert by_id["filesystem"]["status"] == "connected"
        assert by_id["git"]["status"] == "connected"

    而那两份配置**根本没有 command** —— 不可能连上任何东西。
    也就是说，测试把「声称已连接但无法启动」这个状态钉死了：
    有人去修正它反而会让测试变红。

    ★ 教训不是「写错了一个断言」，是**断言的对象选错了**：
      它断言的是「这个字段等于这个值」，而该断言的是
      「**这个字段没有说谎**」。前者会随实现漂移一起被锁住，
      后者不会。

    改成守性质：任何声称 connected 的条目，必须真的可启动。
    """

    services = load_builtin_mcp_services()
    by_id = {str(service["id"]): service for service in services}

    # ★ 断言守的是**性质**而不是清单：第三方应用（GitHub / 飞书 / 支付宝）
    #   会随需求增删，把文件名写死会让每次加一个应用都要改测试 ——
    #   而那条测试并不因此变得更有保障。
    assert {"agentteams", "browser", "filesystem", "git"} <= set(by_id)
    assert by_id["agentteams"]["authentication"] == "missing"
    assert "error" in by_id["agentteams"]

    for service_id, service in by_id.items():
        launchable = (
            service.get("transport") == "stdio"
            and bool(str(service.get("command") or "").strip())
            and bool(service.get("enabled", True))
        )
        if service.get("status") == "connected":
            assert launchable, (
                f"{service_id} 声称 connected，但它不可启动"
                f"（transport={service.get('transport')!r} "
                f"command={service.get('command')!r} enabled={service.get('enabled')!r}）。"
                "状态字段谎报比没有状态字段更糟 —— 界面会把它显示成可用。"
            )


def test_every_stdio_entry_can_actually_be_launched() -> None:
    """★ stdio 却没有 command = 一份**看起来是可执行配置、实际不是**的配置。

    它比声明式清单更坏：声明式清单（transport=http）会被明确地跳过并说明原因，
    而「stdio 但没 command」看上去就该能启动，只是永远不会。

    filesystem / git / browser 三份原先正是这个状态。
    """

    for service in load_builtin_mcp_services():
        if service.get("transport") != "stdio":
            continue
        assert str(service.get("command") or "").strip(), (
            f"{service['id']} 声明 transport=stdio 却没有 command —— "
            "要么补上 command，要么改成声明式清单（换一个 transport）。"
        )


def test_project_mcp_services_writes_deterministic_project_projection(tmp_path: Path) -> None:
    target_dir = tmp_path / ".codentum" / "mcp"

    written = project_mcp_services(target_dir)

    written_names = [path.name for path in written]
    # ★ 守两条性质，而不是写死清单：
    #   ① 四个基础服务都被投影 ② 顺序是确定的（这条才是本测试的名字所指）
    assert {"agentteams.json", "browser.json", "filesystem.json", "git.json"} <= set(written_names)
    assert written_names == sorted(written_names), "投影顺序必须确定，否则每次投影都产生 diff"
    projected = json.loads((target_dir / "filesystem.json").read_text(encoding="utf-8"))
    # ★ 投影必须**逐字保真** —— 这是这个函数唯一的职责，
    #   投影过程中改内容会让界面看到的和磁盘上的不是一回事。
    source = json.loads(
        (Path(__file__).resolve().parents[1] / "mcp" / "filesystem.json").read_text(encoding="utf-8")
    )
    assert projected == source

    # ★ 原先这里断言的是 tools == ["read_file", "write_file", "list_directory"]。
    #   那是**预先罗列的工具清单**，而 playwright.json 里早就写清楚了为什么不能这么做：
    #
    #     「工具清单由 server 在 tools/list 时提供，此处刻意留空 ——
    #       预先罗列会在 server 版本变化后变成谎报。」
    #
    #   这三份配置补上 command 变成真的可启动之后，预先罗列就正式成了那个隐患：
    #   配置里写着三个工具，server 实际给出的是另一批，而**没有任何东西会发现**。
    assert projected["tools"] == [], "可启动的 server 不该预先罗列工具 —— 那会随版本漂移变成谎报"


def test_project_role_skills_writes_deterministic_shared_skill_space(tmp_path: Path) -> None:
    shared_dir = tmp_path / ".codentum" / "skills" / "shared"

    written = project_role_skills(["testing", "frontend", "frontend"], shared_dir)

    assert [(path.parent.name, path.name) for path in written] == [
        ("frontend", "manifest.json"),
        ("frontend", "SKILL.md"),
        ("testing", "manifest.json"),
        ("testing", "SKILL.md"),
    ]
    assert (shared_dir / "frontend" / "manifest.json").exists()
    assert "# Frontend Skill" in (shared_dir / "frontend" / "SKILL.md").read_text(encoding="utf-8")
    assert (shared_dir / "testing" / "manifest.json").exists()
    assert "# Testing Skill" in (shared_dir / "testing" / "SKILL.md").read_text(encoding="utf-8")


def test_builtin_guardian_is_deterministic() -> None:
    guardian = next(spec for spec in load_builtin_role_specs() if spec.id == "guardian")

    assert guardian.usesModel is False
    assert guardian.modelPolicy is None


def test_loader_rejects_guardian_using_model(tmp_path: Path) -> None:
    path = tmp_path / "guardian.json"
    path.write_text(
        json.dumps(
            {
                "id": "guardian",
                "usesModel": True,
                "writes": [],
                "reads": [],
                "tools": [],
                "transitions": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RoleSpecLoadError, match="guardian"):
        load_role_spec_file(path)


def test_loader_rejects_duplicate_role_ids(tmp_path: Path) -> None:
    payload = {
        "id": "coder",
        "usesModel": True,
        "writes": [],
        "reads": [],
        "tools": [],
        "transitions": [],
    }
    (tmp_path / "a.json").write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RoleSpecLoadError, match="重复"):
        load_role_specs_dir(tmp_path)


def test_loader_rejects_missing_skill_ref(tmp_path: Path) -> None:
    payload = {
        "id": "coder",
        "usesModel": True,
        "writes": [],
        "reads": [],
        "tools": [],
        "transitions": [],
        "skills": [{"id": "missing-skill", "scope": "role"}],
    }
    (tmp_path / "coder.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RoleSkillLoadError, match="Skill 不存在"):
        load_role_specs_dir(tmp_path)


def test_loader_rejects_duplicate_skill_refs(tmp_path: Path) -> None:
    payload = {
        "id": "coder",
        "usesModel": True,
        "writes": [],
        "reads": [],
        "tools": [],
        "transitions": [],
        "skills": [
            {"id": "frontend", "scope": "role"},
            {"id": "frontend", "scope": "role"},
        ],
    }
    (tmp_path / "coder.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RoleSkillLoadError, match="重复声明 Skill"):
        load_role_specs_dir(tmp_path)


def test_prompt_ref_rejects_path_traversal(tmp_path: Path) -> None:
    payload = {
        "id": "coder",
        "usesModel": True,
        "writes": [],
        "reads": [],
        "tools": [],
        "transitions": [],
        "promptRef": "../secret.md",
    }
    path = tmp_path / "coder.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    spec = load_role_spec_file(path)

    with pytest.raises(RolePromptLoadError, match="路径穿越"):
        load_role_prompt(spec, prompts_dir=tmp_path)


def test_cloud_catalog_covers_every_role() -> None:
    """★ 云 Skills catalog 覆盖全部 11 个职能角色，且至少有一条全局能力。

    这是「云Skills 打底」承诺的可验证形式：每个子 Agent 都能从云 catalog
    里拿到至少一条相关 Skill，主 Agent / 全局另有通用能力兜底。
    缺了这条测试，catalog 增删 Skill 时可能悄悄漏掉某个角色。
    """
    catalog_path = Path(__file__).resolve().parents[1] / "cloud_skills" / "catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    skills = catalog["skills"]
    assert isinstance(skills, list) and skills, "catalog 必须有 skills 数组"

    for s in skills:
        assert s.get("id") and s.get("name") and s.get("body"), (
            f"Skill 缺 id/name/body: {s.get('id')!r}"
        )

    roles_in_catalog: set[str] = set()
    has_global = False
    for s in skills:
        rs = s.get("roles") or []
        if not rs or "*" in rs:
            has_global = True
        roles_in_catalog.update(str(r) for r in rs)

    all_roles = {spec.id for spec in load_builtin_role_specs()}
    missing = all_roles - roles_in_catalog
    assert not missing, f"云 catalog 未覆盖这些角色：{sorted(missing)}"
    assert has_global, "catalog 至少要有一条全局 Skill（roles 为空或 '*'）"

