"""Convert authorized knowledge resources into MemoryIndex context."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from codentum_contracts import MemoryEntry, PacketId, RetrievalMode, RetrievalQuery
from codentum_contracts.interfaces import MemoryScope
from codentum_contracts.state import RoleId, RoleSpec

from codentum_harness.context_broker import ContextCandidate

from .file_index import MemoryIndexError, PersistentMemoryIndex

__all__ = [
    "KnowledgeSource",
    "ResourceSelectionError",
    "index_knowledge_sources",
    "index_knowledge_sources_now",
    "knowledge_sources_from_payload",
    "memory_context_candidates",
    "memory_context_candidates_now",
]

_MAX_FILE_BYTES = 256 * 1024
_MAX_SOURCE_FILES = 80
_CHUNK_CHARS = 1800
_CHUNK_OVERLAP = 180


class ResourceSelectionError(ValueError):
    """A submitted resource selection cannot be consumed by MemoryIndex."""


@dataclass(frozen=True, slots=True)
class KnowledgeSource:
    """A local file or folder admitted by C/A and consumed by B."""

    selection_id: str
    source_kind: Literal["file", "folder"]
    local_path: Path
    scope_kind: Literal["global", "role", "packet"] = "packet"
    role: RoleId | None = None
    packet_id: PacketId | None = None


async def index_knowledge_sources(
    index: PersistentMemoryIndex,
    sources: Sequence[KnowledgeSource],
    *,
    created_at: str,
) -> tuple[str, ...]:
    """Index authorized sources and return deterministic memory refs."""

    return index_knowledge_sources_now(index, sources, created_at=created_at)


def index_knowledge_sources_now(
    index: PersistentMemoryIndex,
    sources: Sequence[KnowledgeSource],
    *,
    created_at: str,
) -> tuple[str, ...]:
    """Synchronous source indexing for sync worker preparation paths."""

    refs: list[str] = []
    for source in sorted(sources, key=lambda item: (item.selection_id, str(item.local_path))):
        scope = _scope_for(source)
        for relative_path, text in _source_chunks(source):
            for chunk_index, chunk in enumerate(_chunks(text)):
                body = _memory_text(source, relative_path=relative_path, chunk_index=chunk_index, text=chunk)
                entry = MemoryEntry(
                    ref="",
                    level="L0",
                    scope=scope,
                    text=body,
                    created_at=created_at,
                )
                refs.append(index.write_now(entry))
    return tuple(sorted(set(refs)))


async def memory_context_candidates(
    index: PersistentMemoryIndex,
    *,
    query_text: str,
    role_spec: RoleSpec,
    packet_id: PacketId,
    limit: int,
    char_budget: int,
    priority: int = 10,
) -> tuple[ContextCandidate, ...]:
    """Retrieve indexed knowledge and expose it as ContextBroker candidates."""

    return memory_context_candidates_now(
        index,
        query_text=query_text,
        role_spec=role_spec,
        packet_id=packet_id,
        limit=limit,
        char_budget=char_budget,
        priority=priority,
    )


def memory_context_candidates_now(
    index: PersistentMemoryIndex,
    *,
    query_text: str,
    role_spec: RoleSpec,
    packet_id: PacketId,
    limit: int,
    char_budget: int,
    priority: int = 10,
) -> tuple[ContextCandidate, ...]:
    """Synchronous MemoryIndex retrieval for sync worker preparation paths."""

    result = index.retrieve_now(
        RetrievalQuery(
            mode=RetrievalMode.LEXICAL,
            q=query_text,
            scope=MemoryScope(kind="packet", role=role_spec.id, packet_id=packet_id),
            limit=limit,
            char_budget=char_budget,
            min_level="L0",
        )
    )
    candidates: list[ContextCandidate] = []
    for offset, entry in enumerate(result.entries):
        candidates.append(
            ContextCandidate(
                ref=f"memory:{entry.ref}",
                artifact_path=f".codentum/memory/retrieval/{packet_id}/{_safe_ref(entry.ref)}.md",
                text=_context_text(entry, index_version=result.index_version, degraded=result.degraded),
                summary=_context_summary(entry, index_version=result.index_version),
                priority=priority + offset,
            )
        )
    return tuple(candidates)


def knowledge_sources_from_payload(
    payload: Mapping[str, object],
    *,
    packet_id: PacketId,
    role: RoleId,
) -> tuple[KnowledgeSource, ...]:
    """Parse `codentum.resource-selection.v1` knowledge selections."""

    if payload.get("resourceSelectionContract") != "codentum.resource-selection.v1":
        return ()
    selections = payload.get("resourceSelections")
    if selections is None:
        return ()
    if not isinstance(selections, list):
        raise ResourceSelectionError("resourceSelections must be a list")

    sources: list[KnowledgeSource] = []
    for selection in selections:
        if not isinstance(selection, dict):
            raise ResourceSelectionError("resource selection must be an object")
        item = cast(dict[str, object], selection)
        if item.get("kind") != "knowledge":
            continue
        source_kind = item.get("sourceKind")
        if source_kind not in {"file", "folder"}:
            continue
        local_path = item.get("localPath")
        if not isinstance(local_path, str) or not local_path:
            raise ResourceSelectionError("knowledge resource localPath is required")
        selection_id = item.get("id")
        if not isinstance(selection_id, str) or not selection_id.startswith("managed:"):
            raise ResourceSelectionError("knowledge resource id is invalid")
        sources.append(
            KnowledgeSource(
                selection_id=selection_id,
                source_kind=cast(Literal["file", "folder"], source_kind),
                local_path=Path(local_path),
                scope_kind=_scope_kind(item.get("scope")),
                role=role,
                packet_id=packet_id,
            )
        )
    return tuple(sources)


def _scope_for(source: KnowledgeSource) -> MemoryScope:
    if source.scope_kind == "global":
        return MemoryScope(kind="global")
    if source.scope_kind == "role":
        return MemoryScope(kind="role", role=source.role)
    return MemoryScope(kind="packet", packet_id=source.packet_id)


def _source_chunks(source: KnowledgeSource) -> tuple[tuple[str, str], ...]:
    root = source.local_path
    try:
        if source.source_kind == "file":
            if not root.is_file():
                raise ResourceSelectionError(f"knowledge file does not exist: {root}")
            return ((root.name, _read_text_file(root)),)
        if not root.is_dir():
            raise ResourceSelectionError(f"knowledge folder does not exist: {root}")
        chunks: list[tuple[str, str]] = []
        for path in _walk_files(root):
            chunks.append((path.relative_to(root).as_posix(), _read_text_file(path)))
        return tuple(chunks)
    except OSError as exc:
        raise ResourceSelectionError(f"cannot read knowledge source: {root}") from exc


def _walk_files(root: Path) -> Iterable[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ResourceSelectionError(f"knowledge folder contains symlink: {path}")
        if path.is_file():
            files.append(path)
            if len(files) > _MAX_SOURCE_FILES:
                raise ResourceSelectionError(f"knowledge folder contains more than {_MAX_SOURCE_FILES} files")
    return tuple(files)


def _read_text_file(path: Path) -> str:
    if path.stat().st_size > _MAX_FILE_BYTES:
        raise ResourceSelectionError(f"knowledge file exceeds {_MAX_FILE_BYTES} bytes: {path}")
    data = path.read_bytes()
    if b"\x00" in data:
        raise ResourceSelectionError(f"knowledge file is not text: {path}")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ResourceSelectionError(f"knowledge file must be utf-8 text: {path}") from exc
    if not text.strip():
        raise ResourceSelectionError(f"knowledge file is empty: {path}")
    return text


def _chunks(text: str) -> tuple[str, ...]:
    stripped = text.strip()
    if len(stripped) <= _CHUNK_CHARS:
        return (stripped,)
    chunks: list[str] = []
    start = 0
    while start < len(stripped):
        end = min(len(stripped), start + _CHUNK_CHARS)
        chunks.append(stripped[start:end].strip())
        if end == len(stripped):
            break
        start = max(0, end - _CHUNK_OVERLAP)
    return tuple(chunk for chunk in chunks if chunk)


def _memory_text(
    source: KnowledgeSource,
    *,
    relative_path: str,
    chunk_index: int,
    text: str,
) -> str:
    metadata = {
        "selection_id": source.selection_id,
        "source_kind": source.source_kind,
        "source_path": str(source.local_path),
        "relative_path": relative_path,
        "chunk_index": chunk_index,
    }
    return (
        "--- memory-source ---\n"
        f"{json.dumps(metadata, ensure_ascii=False, sort_keys=True)}\n"
        "--- content ---\n"
        f"{text}"
    )


def _context_text(entry: MemoryEntry, *, index_version: str, degraded: bool) -> str:
    return (
        f"indexVersion: {index_version}\n"
        f"memoryRef: {entry.ref}\n"
        f"level: {entry.level}\n"
        f"degraded: {str(degraded).lower()}\n\n"
        f"{entry.text}"
    )


def _context_summary(entry: MemoryEntry, *, index_version: str) -> str:
    return f"indexVersion: {index_version}\nmemoryRef: {entry.ref}\nlevel: {entry.level}\n"


def _scope_kind(value: object) -> Literal["global", "role", "packet"]:
    if value == "global":
        return "global"
    if value == "role":
        return "role"
    return "packet"


def _safe_ref(ref: str) -> str:
    if not ref.startswith("mem:sha256:"):
        raise MemoryIndexError(f"invalid memory ref for context candidate: {ref}")
    return ref.removeprefix("mem:sha256:")
