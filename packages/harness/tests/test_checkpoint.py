from __future__ import annotations

import json
from pathlib import Path

from codentum_contracts import BudgetGrantRuntime, ModelRouting, PacketId, RoleSpec, SpawnRequest
from codentum_harness.checkpoint import write_initial_checkpoint
from codentum_harness.context_broker import ContextCandidate, assemble_context_bundle


def request(workspace: Path) -> SpawnRequest:
    return SpawnRequest(
        packet_id=PacketId("wp-abcdef"),
        role="coder",
        mounts=(),
        tools=("read_file", "write_file"),
        routing=ModelRouting(model="qwen-plus", effort="medium"),
        budget=BudgetGrantRuntime(limit_usd=1.0, degradation_chain=("summary", "reference")),
        workspace=str(workspace),
        attempt=1,
    )


def role_spec() -> RoleSpec:
    return RoleSpec(
        id="coder",
        usesModel=True,
        writes=("workspace/src/**",),
        reads=("packages/contracts/**",),
        tools=("read_file", "write_file"),
        transitions=(),
    )


def test_initial_checkpoint_is_stable_for_same_input(tmp_path: Path) -> None:
    req = request(tmp_path / "worker")
    context = assemble_context_bundle(
        role_spec(),
        candidates=(
            ContextCandidate(
                ref="packet",
                artifact_path=".codentum/backlog/packets/wp-abcdef.yaml",
                text="implement app shell",
                required=True,
            ),
        ),
        char_budget=100,
    )

    first = write_initial_checkpoint(
        worker_id="wp-abcdef-attempt-1",
        request=req,
        context=context,
        evidence_dir=tmp_path / "evidence-a",
    )
    second = write_initial_checkpoint(
        worker_id="wp-abcdef-attempt-1",
        request=req,
        context=context,
        evidence_dir=tmp_path / "evidence-b",
    )

    assert first == second

    record = json.loads((tmp_path / "evidence-a" / "checkpoints" / "0000.json").read_text())
    assert record["digest"] == first.digest
    assert record["seq"] == 0
    assert record["request"]["packet_id"] == "wp-abcdef"
    assert record["context"]["slices"][0]["ref"] == "packet"


def test_initial_checkpoint_digest_changes_when_context_changes(tmp_path: Path) -> None:
    req = request(tmp_path / "worker")
    original = assemble_context_bundle(
        role_spec(),
        candidates=(ContextCandidate(ref="packet", artifact_path=".codentum/packet.yaml", text="a"),),
        char_budget=100,
    )
    changed = assemble_context_bundle(
        role_spec(),
        candidates=(ContextCandidate(ref="packet", artifact_path=".codentum/packet.yaml", text="b"),),
        char_budget=100,
    )

    first = write_initial_checkpoint(
        worker_id="wp-abcdef-attempt-1",
        request=req,
        context=original,
        evidence_dir=tmp_path / "evidence-a",
    )
    second = write_initial_checkpoint(
        worker_id="wp-abcdef-attempt-1",
        request=req,
        context=changed,
        evidence_dir=tmp_path / "evidence-b",
    )

    assert first.digest != second.digest
