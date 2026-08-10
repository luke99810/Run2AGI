from __future__ import annotations

import json
from pathlib import Path

import pytest
from codentum_roles import (
    RolePromptLoadError,
    RoleSpecLoadError,
    load_builtin_role_specs,
    load_role_prompt,
    load_role_spec_file,
    load_role_specs_dir,
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
