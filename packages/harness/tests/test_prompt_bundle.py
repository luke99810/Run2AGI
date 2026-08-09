from __future__ import annotations

import json
from pathlib import Path

import pytest
from codentum_contracts import BudgetGrantRuntime, ModelRouting, PacketId, RoleId, RoleSpec, SpawnRequest
from codentum_harness.context_broker import ContextCandidate, assemble_context_bundle
from codentum_harness.prompt_bundle import (
    PromptBundleError,
    assemble_worker_prompt_bundle,
    write_worker_prompt_bundle,
)


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
    # ★ 必须显式 encoding="utf-8"。render.py 是按 UTF-8 写的，
    #   而 read_text() 不带参数走的是平台首选编码 —— 在中文 Windows 上
    #   是 cp936，于是路径里的非 ASCII 字符读回来是乱码。
    #   这不是测试挑剔：证据文件要能跨机器复算，两端就必须锁死同一个编码。
    prompt_dir = tmp_path / "evidence-a" / "prompt"
    manifest = json.loads((prompt_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["digest"] == first.digest
    assert manifest["context_refs"] == ["diff"]
    assert (prompt_dir / "system.md").read_text(encoding="utf-8") == first.system
    assert (prompt_dir / "user.md").read_text(encoding="utf-8") == first.user


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
