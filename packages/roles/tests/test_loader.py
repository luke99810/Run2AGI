from __future__ import annotations

import json
from pathlib import Path

import pytest
from codentum_roles import (
    RoleSpecLoadError,
    load_builtin_role_specs,
    load_role_spec_file,
    load_role_specs_dir,
)


def test_builtin_rolespecs_load() -> None:
    specs = load_builtin_role_specs()
    assert {spec.id for spec in specs} >= {"coder", "reviewer", "qa"}


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
