from __future__ import annotations

import pytest
from codentum_contracts import RoleSpec
from codentum_harness.context_broker import (
    ContextBudgetError,
    ContextCandidate,
    ContextVisibilityError,
    assemble_context_bundle,
)


def role_spec(*, invisible: tuple[str, ...] = ()) -> RoleSpec:
    return RoleSpec(
        id="reviewer",
        usesModel=True,
        writes=(),
        reads=("workspace/**", "evidence/review/**"),
        invisible=invisible,
        tools=("read_file",),
        transitions=(),
    )


def test_context_broker_filters_invisible_before_budget() -> None:
    bundle = assemble_context_bundle(
        role_spec(invisible=("evidence/coder/**",)),
        candidates=(
            ContextCandidate(
                ref="diff",
                artifact_path="workspace/src/app.py",
                text="change diff",
                required=True,
                priority=1,
            ),
            ContextCandidate(
                ref="coder-thoughts",
                artifact_path="evidence/coder/wp-1/thoughts.jsonl",
                text="private reasoning",
                priority=2,
            ),
        ),
        char_budget=100,
    )

    assert bundle.refs == ("diff",)
    assert [item.ref for item in bundle.denied] == ["coder-thoughts"]
    assert bundle.char_total == len("change diff")


def test_context_broker_degrades_to_summary_then_reference_without_truncating() -> None:
    bundle = assemble_context_bundle(
        role_spec(),
        candidates=(
            ContextCandidate(
                ref="contract",
                artifact_path="packages/contracts/schemas/workpacket.schema.json",
                text="x" * 80,
                summary="contract summary",
                priority=1,
            ),
            ContextCandidate(
                ref="log",
                artifact_path="logs/test.log",
                text="y" * 80,
                priority=2,
            ),
        ),
        char_budget=70,
    )

    assert [slice_.mode for slice_ in bundle.slices] == ["summary", "reference"]
    assert bundle.slices[0].text == "contract summary"
    assert bundle.slices[1].text == "ref:log\npath:logs/test.log\n"
    assert bundle.degraded is True


def test_required_invisible_context_fails_closed() -> None:
    with pytest.raises(ContextVisibilityError, match="required context"):
        assemble_context_bundle(
            role_spec(invisible=("evidence/coder/**",)),
            candidates=(
                ContextCandidate(
                    ref="coder-private",
                    artifact_path="evidence/coder/wp-1/thoughts.jsonl",
                    text="private reasoning",
                    required=True,
                ),
            ),
            char_budget=100,
        )


def test_required_context_that_cannot_fit_reference_budget_fails_closed() -> None:
    with pytest.raises(ContextBudgetError, match="cannot fit budget"):
        assemble_context_bundle(
            role_spec(),
            candidates=(
                ContextCandidate(
                    ref="acceptance",
                    artifact_path="tests/acceptance/test_app.py",
                    text="x" * 100,
                    required=True,
                ),
            ),
            char_budget=5,
        )


def test_context_broker_orders_by_priority_then_ref() -> None:
    bundle = assemble_context_bundle(
        role_spec(),
        candidates=(
            ContextCandidate(ref="b", artifact_path="workspace/b.py", text="b", priority=2),
            ContextCandidate(ref="a", artifact_path="workspace/a.py", text="a", priority=2),
            ContextCandidate(ref="packet", artifact_path=".codentum/packet.yaml", text="p", priority=1),
        ),
        char_budget=10,
    )

    assert bundle.refs == ("packet", "a", "b")
