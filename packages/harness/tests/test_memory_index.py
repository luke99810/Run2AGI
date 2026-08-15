from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from codentum_contracts import MemoryEntry, RetrievalMode, RetrievalQuery
from codentum_contracts.interfaces import MemoryScope, PromotionJustification
from codentum_contracts.state import PacketId, RoleSpec
from codentum_harness.context_broker import assemble_context_bundle
from codentum_harness.memory_index import (
    KnowledgeSource,
    MemoryIndexConflictError,
    PersistentMemoryIndex,
    ResourceSelectionError,
    index_knowledge_sources,
    knowledge_sources_from_payload,
    memory_context_candidates,
)


def test_memory_index_retrieval_is_deterministic_for_same_version(tmp_path: Path) -> None:
    index = PersistentMemoryIndex(tmp_path / "memory")
    asyncio.run(
        index.write(
            MemoryEntry(
                ref="",
                level="L0",
                scope=MemoryScope(kind="packet", packet_id=PacketId("wp-abcdef")),
                text="alpha beta release notes",
                created_at="2026-08-13T00:00:00Z",
            )
        )
    )
    query = RetrievalQuery(
        mode=RetrievalMode.LEXICAL,
        q="beta",
        scope=MemoryScope(kind="packet", packet_id=PacketId("wp-abcdef")),
        limit=5,
        char_budget=100,
        min_level="L0",
    )

    first = asyncio.run(index.retrieve(query))
    second = asyncio.run(index.retrieve(query))

    assert first.index_version == second.index_version
    assert [entry.ref for entry in first.entries] == [entry.ref for entry in second.entries]
    assert first.entries[0].text == "alpha beta release notes"


def test_memory_index_promote_is_monotonic_and_l3_requires_gate(tmp_path: Path) -> None:
    index = PersistentMemoryIndex(tmp_path / "memory")
    ref = asyncio.run(
        index.write(
            MemoryEntry(
                ref="",
                level="L1",
                scope=MemoryScope(kind="global"),
                text="Prefer deterministic retrieval before semantic fallback.",
                created_at="2026-08-13T00:00:00Z",
            )
        )
    )

    with pytest.raises(MemoryIndexConflictError, match="requires a falsification_gate"):
        asyncio.run(
            index.promote(
                ref,
                "L3",
                PromotionJustification(kind="operator", detail="manual", refs=()),
            )
        )

    asyncio.run(
        index.promote(
            ref,
            "L3",
            PromotionJustification(kind="falsification_gate", detail="passed review", refs=("case-1",)),
        )
    )
    with pytest.raises(MemoryIndexConflictError, match="cannot demote"):
        asyncio.run(
            index.promote(
                ref,
                "L2",
                PromotionJustification(kind="operator", detail="try demote", refs=()),
            )
        )


def test_knowledge_source_payload_indexes_only_selected_knowledge(tmp_path: Path) -> None:
    knowledge = tmp_path / "notes.md"
    knowledge.write_text("Billing screen must show CNY cost attribution.\n", encoding="utf-8")
    payload = {
        "resourceSelectionContract": "codentum.resource-selection.v1",
        "resourceSelections": [
            {
                "id": "managed:00000000-0000-0000-0000-000000000001",
                "kind": "knowledge",
                "scope": "project",
                "sourceKind": "file",
                "localPath": str(knowledge),
            },
            {
                "id": "managed:00000000-0000-0000-0000-000000000002",
                "kind": "skill",
                "scope": "role",
                "roleId": "coder",
                "sourceKind": "file",
                "localPath": str(knowledge),
            },
        ],
    }

    sources = knowledge_sources_from_payload(payload, packet_id=PacketId("wp-abcdef"), role="coder")
    index = PersistentMemoryIndex(tmp_path / "memory")
    refs = asyncio.run(index_knowledge_sources(index, sources, created_at="2026-08-13T00:00:00Z"))
    candidates = asyncio.run(
        memory_context_candidates(
            index,
            query_text="CNY cost",
            role_spec=role_spec(),
            packet_id=PacketId("wp-abcdef"),
            limit=3,
            char_budget=500,
        )
    )

    assert len(sources) == 1
    assert len(refs) == 1
    assert [candidate.ref for candidate in candidates] == [f"memory:{refs[0]}"]
    assert "indexVersion: sha256:" in candidates[0].text
    assert "Billing screen must show CNY cost attribution." in candidates[0].text


def test_knowledge_source_rejects_binary_files(tmp_path: Path) -> None:
    knowledge = tmp_path / "blob.bin"
    knowledge.write_bytes(b"hello\x00world")

    with pytest.raises(ResourceSelectionError, match="not text"):
        asyncio.run(
            index_knowledge_sources(
                PersistentMemoryIndex(tmp_path / "memory"),
                (
                    KnowledgeSource(
                        selection_id="managed:00000000-0000-0000-0000-000000000001",
                        source_kind="file",
                        local_path=knowledge,
                        packet_id=PacketId("wp-abcdef"),
                    ),
                ),
                created_at="2026-08-13T00:00:00Z",
            )
        )


def test_role_scoped_memory_is_visible_only_to_that_role(tmp_path: Path) -> None:
    coder_note = tmp_path / "coder.md"
    reviewer_note = tmp_path / "reviewer.md"
    coder_note.write_text("Coder should inspect subscription CNY totals.\n", encoding="utf-8")
    reviewer_note.write_text("Reviewer should inspect subscription CNY totals.\n", encoding="utf-8")
    index = PersistentMemoryIndex(tmp_path / "memory")
    asyncio.run(
        index_knowledge_sources(
            index,
            (
                KnowledgeSource(
                    selection_id="managed:00000000-0000-0000-0000-000000000001",
                    source_kind="file",
                    local_path=coder_note,
                    scope_kind="role",
                    role="coder",
                    packet_id=PacketId("wp-abcdef"),
                ),
                KnowledgeSource(
                    selection_id="managed:00000000-0000-0000-0000-000000000002",
                    source_kind="file",
                    local_path=reviewer_note,
                    scope_kind="role",
                    role="reviewer",
                    packet_id=PacketId("wp-abcdef"),
                ),
            ),
            created_at="2026-08-13T00:00:00Z",
        )
    )

    candidates = asyncio.run(
        memory_context_candidates(
            index,
            query_text="subscription CNY",
            role_spec=role_spec(),
            packet_id=PacketId("wp-abcdef"),
            limit=5,
            char_budget=1000,
        )
    )

    assert len(candidates) == 1
    assert "Coder should inspect" in candidates[0].text
    assert "Reviewer should inspect" not in candidates[0].text


def test_memory_candidates_still_obey_context_broker_visibility(tmp_path: Path) -> None:
    knowledge = tmp_path / "reviewer-private.md"
    knowledge.write_text("Reviewer-only note with beta details.\n", encoding="utf-8")
    index = PersistentMemoryIndex(tmp_path / "memory")
    asyncio.run(
        index_knowledge_sources(
            index,
            (
                KnowledgeSource(
                    selection_id="managed:00000000-0000-0000-0000-000000000001",
                    source_kind="file",
                    local_path=knowledge,
                    packet_id=PacketId("wp-abcdef"),
                ),
            ),
            created_at="2026-08-13T00:00:00Z",
        )
    )
    candidates = asyncio.run(
        memory_context_candidates(
            index,
            query_text="beta",
            role_spec=role_spec(invisible=(".codentum/memory/retrieval/**",)),
            packet_id=PacketId("wp-abcdef"),
            limit=3,
            char_budget=500,
        )
    )

    bundle = assemble_context_bundle(
        role_spec(invisible=(".codentum/memory/retrieval/**",)),
        candidates=candidates,
        char_budget=500,
    )

    assert bundle.slices == ()
    assert [item.ref for item in bundle.denied] == [candidates[0].ref]


def role_spec(*, invisible: tuple[str, ...] = ()) -> RoleSpec:
    return RoleSpec(
        id="coder",
        usesModel=True,
        writes=("workspace/**",),
        reads=("workspace/**",),
        invisible=invisible,
        tools=("read_file", "write_file"),
        transitions=(),
    )


def test_semantic_retrieval_is_deterministic(tmp_path: Path) -> None:
    index = PersistentMemoryIndex(tmp_path / "memory")
    for text in ("beta release notes", "gamma release notes", "beta quaternion octonion"):
        asyncio.run(
            index.write(
                MemoryEntry(
                    ref="",
                    level="L0",
                    scope=MemoryScope(kind="global"),
                    text=text,
                    created_at="2026-08-15T00:00:00Z",
                )
            )
        )
    query = RetrievalQuery(
        mode=RetrievalMode.SEMANTIC,
        q="release quaternion",
        scope=MemoryScope(kind="global"),
        limit=5,
        char_budget=1000,
    )

    first = asyncio.run(index.retrieve(query))
    second = asyncio.run(index.retrieve(query))

    assert first.index_version == second.index_version
    assert [entry.ref for entry in first.entries] == [entry.ref for entry in second.entries]
    assert first.degraded is False


def test_semantic_mode_weights_rare_terms_above_common_ones(tmp_path: Path) -> None:
    """稀有词（df 低）比常见词（df 高）更值钱 —— idf 加权是向量检索的核心。

    LEXICAL 会把三者都判成「命中 1 个词」而并列；SEMANTIC 因 rareword 的
    idf 更高，把只含 rareword 的 entry 排到只含 commonword 的前面。
    """
    index = PersistentMemoryIndex(tmp_path / "memory")
    refs: list[str] = []
    for text in (
        "commonword appears here",
        "commonword appears there",
        "rareword appears here",
    ):
        refs.append(
            asyncio.run(
                index.write(
                    MemoryEntry(
                        ref="",
                        level="L0",
                        scope=MemoryScope(kind="global"),
                        text=text,
                        created_at="2026-08-15T00:00:00Z",
                    )
                )
            )
        )

    query = RetrievalQuery(
        mode=RetrievalMode.SEMANTIC,
        q="commonword rareword",
        scope=MemoryScope(kind="global"),
        limit=5,
        char_budget=1000,
    )
    result = asyncio.run(index.retrieve(query))

    assert next(entry.ref for entry in result.entries) == refs[2]
    assert result.degraded is False


def test_semantic_returns_empty_when_no_term_overlap(tmp_path: Path) -> None:
    index = PersistentMemoryIndex(tmp_path / "memory")
    asyncio.run(
        index.write(
            MemoryEntry(
                ref="",
                level="L0",
                scope=MemoryScope(kind="global"),
                text="alpha beta gamma",
                created_at="2026-08-15T00:00:00Z",
            )
        )
    )
    query = RetrievalQuery(
        mode=RetrievalMode.SEMANTIC,
        q="totally unrelated terms",
        scope=MemoryScope(kind="global"),
        limit=5,
        char_budget=1000,
    )
    result = asyncio.run(index.retrieve(query))
    assert result.entries == ()


def test_knowledge_candidates_can_use_semantic_mode(tmp_path: Path) -> None:
    knowledge = tmp_path / "notes.md"
    knowledge.write_text("Billing screen must show CNY cost attribution.\n", encoding="utf-8")
    index = PersistentMemoryIndex(tmp_path / "memory")
    asyncio.run(
        index_knowledge_sources(
            index,
            (KnowledgeSource(
                selection_id="managed:00000000-0000-0000-0000-000000000001",
                source_kind="file",
                local_path=knowledge,
                packet_id=PacketId("wp-abcdef"),
            ),),
            created_at="2026-08-15T00:00:00Z",
        )
    )
    candidates = asyncio.run(
        memory_context_candidates(
            index,
            query_text="CNY cost attribution",
            role_spec=role_spec(),
            packet_id=PacketId("wp-abcdef"),
            limit=3,
            char_budget=500,
            mode=RetrievalMode.SEMANTIC,
        )
    )
    assert len(candidates) == 1
    assert "Billing screen must show CNY cost attribution." in candidates[0].text

