from __future__ import annotations

import json
from pathlib import Path

import pytest
from codentum_contracts import BudgetGrantRuntime, ModelRouting, PacketId, RoleId, RoleSpec, SpawnRequest
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
    assert "### testing" in bundle.system
    assert "# Testing Skill" in bundle.system

    manifest = json.loads(
        (tmp_path / "evidence" / "prompt" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["skill_refs"] == ["frontend", "testing"]


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
            {"id": "frontend", "scope": "role", "state": "candidate"},
            {"id": "testing", "scope": "role", "state": "active"},
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
