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
    assert skills_by_role["integrator"] == ("integration", "testing", "review", "debugging")
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
    services = load_builtin_mcp_services()
    by_id = {str(service["id"]): service for service in services}

    assert sorted(by_id) == ["agentteams", "browser", "filesystem", "git"]
    assert by_id["filesystem"]["status"] == "connected"
    assert by_id["git"]["status"] == "connected"
    assert by_id["browser"]["status"] == "disconnected"
    assert by_id["agentteams"]["authentication"] == "missing"
    assert "error" in by_id["agentteams"]


def test_project_mcp_services_writes_deterministic_project_projection(tmp_path: Path) -> None:
    target_dir = tmp_path / ".codentum" / "mcp"

    written = project_mcp_services(target_dir)

    assert [path.name for path in written] == [
        "agentteams.json",
        "browser.json",
        "filesystem.json",
        "git.json",
    ]
    projected = json.loads((target_dir / "filesystem.json").read_text(encoding="utf-8"))
    assert projected["tools"] == ["read_file", "write_file", "list_directory"]


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
