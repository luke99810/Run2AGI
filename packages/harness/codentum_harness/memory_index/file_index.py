"""File-backed implementation of the frozen MemoryIndex protocol."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from codentum_contracts import (
    MemoryEntry,
    MemoryLevel,
    RetrievalMode,
    RetrievalQuery,
    RetrievalResult,
)
from codentum_contracts.interfaces import MemoryScope, PromotionJustification

__all__ = [
    "MemoryIndexConflictError",
    "MemoryIndexError",
    "MemoryIndexNotFoundError",
    "PersistentMemoryIndex",
]

_LEVEL_ORDER: dict[MemoryLevel, int] = {"L0": 0, "L1": 1, "L2": 2, "L3": 3, "L4": 4}
_WORD_RE = re.compile(r"[0-9A-Za-z_\u4e00-\u9fff]+")


class MemoryIndexError(ValueError):
    """A persistent memory index operation cannot be completed safely."""


class MemoryIndexNotFoundError(MemoryIndexError):
    """The requested memory ref is not present in the index."""


class MemoryIndexConflictError(MemoryIndexError):
    """A write or promotion would violate append-safe memory rules."""


class PersistentMemoryIndex:
    """Small deterministic MemoryIndex backed by JSON files.

    This implementation intentionally avoids vector retrieval. The semantic mode
    returns a degraded lexical fallback so callers can see that the least
    deterministic tier was requested without pretending embeddings exist.
    """

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)
        self._entries_dir = self._root / "entries"
        self._events_file = self._root / "events.jsonl"

    async def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        """Return deterministic results for a frozen RetrievalQuery."""

        return self.retrieve_now(query)

    def retrieve_now(self, query: RetrievalQuery) -> RetrievalResult:
        """Synchronous retrieval for sync worker preparation paths."""

        if query.limit < 1:
            raise MemoryIndexError("retrieval limit must be positive")
        if query.char_budget < 1:
            raise MemoryIndexError("retrieval char_budget must be positive")

        entries = self._eligible_entries(query)
        ranked = _rank(entries, query)
        selected, degraded_by_budget = _fit_budget(ranked[: query.limit], query.char_budget)
        degraded = degraded_by_budget or query.mode == RetrievalMode.SEMANTIC
        return RetrievalResult(
            entries=tuple(selected),
            index_version=self.version_now(),
            degraded=degraded,
        )

    async def write(self, entry: MemoryEntry) -> str:
        """Persist an entry by content-addressed ref and return that ref."""

        return self.write_now(entry)

    def write_now(self, entry: MemoryEntry) -> str:
        """Synchronous write for sync worker preparation paths."""

        computed_ref = _entry_ref(entry)
        normalized = _normalize_entry(entry if entry.ref else replace(entry, ref=computed_ref))
        if normalized.ref != computed_ref:
            raise MemoryIndexError("memory entry ref must match its canonical content digest")

        self._entries_dir.mkdir(parents=True, exist_ok=True)
        path = self._entry_path(normalized.ref)
        payload = _entry_to_json(normalized)
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if existing != encoded:
                raise MemoryIndexConflictError(
                    f"memory ref already exists with different content: {normalized.ref}"
                )
            return normalized.ref
        path.write_text(encoded, encoding="utf-8")
        self._append_event("write", normalized.ref, {"level": normalized.level})
        return normalized.ref

    async def promote(
        self,
        ref: str,
        to: MemoryLevel,
        justification: PromotionJustification,
    ) -> None:
        """Promote an entry monotonically and keep the same ref."""

        self.promote_now(ref, to, justification)

    def promote_now(
        self,
        ref: str,
        to: MemoryLevel,
        justification: PromotionJustification,
    ) -> None:
        """Synchronous promotion for sync worker preparation paths."""

        entry = self._load_ref(ref)
        if _LEVEL_ORDER[to] < _LEVEL_ORDER[entry.level]:
            raise MemoryIndexConflictError(f"cannot demote memory {ref} from {entry.level} to {to}")
        if to == "L3" and justification.kind != "falsification_gate":
            raise MemoryIndexConflictError("promotion to L3 requires a falsification_gate justification")
        if to == entry.level:
            return
        promoted = replace(entry, level=to)
        self._entry_path(ref).write_text(
            json.dumps(_entry_to_json(promoted), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        self._append_event(
            "promote",
            ref,
            {
                "from": entry.level,
                "to": to,
                "justification": _justification_to_json(justification),
            },
        )

    async def record_hit(self, ref: str, helpful: bool) -> None:
        """Record retrieval feedback without changing the entry identity."""

        self.record_hit_now(ref, helpful)

    def record_hit_now(self, ref: str, helpful: bool) -> None:
        """Synchronous hit feedback for sync worker preparation paths."""

        entry = self._load_ref(ref)
        updated = replace(
            entry,
            hits=entry.hits + 1,
            helpful=entry.helpful + (1 if helpful else 0),
        )
        self._entry_path(ref).write_text(
            json.dumps(_entry_to_json(updated), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        self._append_event("hit", ref, {"helpful": helpful})

    async def version(self) -> str:
        """Return a stable digest of all current entries."""

        return self.version_now()

    def version_now(self) -> str:
        """Synchronous stable digest of all current entries."""

        payload = [_entry_to_json(entry) for entry in self._all_entries()]
        return _digest({"schema_version": 1, "entries": payload})

    @staticmethod
    def ref_for(
        *,
        level: MemoryLevel,
        scope: MemoryScope,
        text: str,
        created_at: str,
    ) -> str:
        """Compute the canonical ref for a new entry before write()."""

        return _entry_ref(
            MemoryEntry(
                ref="",
                level=level,
                scope=scope,
                text=text,
                created_at=created_at,
            )
        )

    def _eligible_entries(self, query: RetrievalQuery) -> list[MemoryEntry]:
        min_level = query.min_level
        return [
            entry
            for entry in self._all_entries()
            if _scope_matches(entry.scope, query.scope)
            and (min_level is None or _LEVEL_ORDER[entry.level] >= _LEVEL_ORDER[min_level])
        ]

    def _all_entries(self) -> list[MemoryEntry]:
        if not self._entries_dir.exists():
            return []
        entries: list[MemoryEntry] = []
        for path in sorted(self._entries_dir.glob("*.json")):
            entries.append(_entry_from_json(_read_json_object(path)))
        return sorted(entries, key=lambda item: item.ref)

    def _load_ref(self, ref: str) -> MemoryEntry:
        path = self._entry_path(ref)
        if not path.exists():
            raise MemoryIndexNotFoundError(f"memory ref not found: {ref}")
        return _entry_from_json(_read_json_object(path))

    def _entry_path(self, ref: str) -> Path:
        if not ref.startswith("mem:sha256:"):
            raise MemoryIndexError(f"invalid memory ref: {ref!r}")
        return self._entries_dir / f"{ref.removeprefix('mem:sha256:')}.json"

    def _append_event(self, kind: str, ref: str, payload: dict[str, object]) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        record = {"kind": kind, "ref": ref, "payload": payload}
        with self._events_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _normalize_entry(entry: MemoryEntry) -> MemoryEntry:
    if not entry.text:
        raise MemoryIndexError("memory entry text must not be empty")
    if entry.hits < 0 or entry.helpful < 0:
        raise MemoryIndexError("memory entry hit counters must be non-negative")
    if entry.helpful > entry.hits:
        raise MemoryIndexError("memory entry helpful counter cannot exceed hits")
    return entry


def _rank(entries: list[MemoryEntry], query: RetrievalQuery) -> list[MemoryEntry]:
    if query.mode == RetrievalMode.EXACT:
        ranked = [entry for entry in entries if entry.ref == query.q or query.q in _exact_keys(entry)]
        return sorted(ranked, key=lambda item: item.ref)

    if query.mode == RetrievalMode.STRUCTURAL:
        terms = _tokens(query.q)
        ranked = [entry for entry in entries if _structural_score(entry, terms) > 0]
        return sorted(ranked, key=lambda item: (-_structural_score(item, terms), item.ref))

    terms = _tokens(query.q)
    ranked = [entry for entry in entries if _lexical_score(entry, terms) > 0]
    return sorted(ranked, key=lambda item: (-_lexical_score(item, terms), item.ref))


def _fit_budget(entries: list[MemoryEntry], char_budget: int) -> tuple[list[MemoryEntry], bool]:
    selected: list[MemoryEntry] = []
    spent = 0
    degraded = False
    for entry in entries:
        chars = len(entry.text)
        if spent + chars <= char_budget:
            selected.append(entry)
            spent += chars
        else:
            degraded = True
            break
    return selected, degraded


def _scope_matches(entry_scope: MemoryScope, query_scope: MemoryScope) -> bool:
    if query_scope.kind == "global":
        return entry_scope.kind == "global"
    if query_scope.kind == "role":
        return (
            entry_scope.kind == "global"
            or (entry_scope.kind == "role" and entry_scope.role == query_scope.role)
        )
    if query_scope.kind == "packet":
        return (
            entry_scope.kind == "global"
            or (entry_scope.kind == "role" and entry_scope.role == query_scope.role)
            or (entry_scope.kind == "packet" and entry_scope.packet_id == query_scope.packet_id)
        )
    return False


def _exact_keys(entry: MemoryEntry) -> tuple[str, ...]:
    return (entry.ref, _scope_key(entry.scope))


def _structural_score(entry: MemoryEntry, terms: frozenset[str]) -> int:
    haystack = _tokens(" ".join((entry.ref, _scope_key(entry.scope), entry.created_at)))
    return len(terms & haystack)


def _lexical_score(entry: MemoryEntry, terms: frozenset[str]) -> int:
    if not terms:
        return 0
    haystack = _tokens(entry.text)
    return len(terms & haystack)


def _tokens(text: str) -> frozenset[str]:
    tokens: set[str] = set()
    for match in _WORD_RE.finditer(text):
        token = match.group(0).lower()
        tokens.add(token)
        if _is_cjk_token(token):
            tokens.update(_ngrams(token, min_size=2, max_size=6))
    return frozenset(tokens)


def _is_cjk_token(token: str) -> bool:
    return all("\u4e00" <= char <= "\u9fff" for char in token)


def _ngrams(token: str, *, min_size: int, max_size: int) -> set[str]:
    grams: set[str] = set()
    for size in range(min_size, min(max_size, len(token)) + 1):
        grams.update(token[start : start + size] for start in range(0, len(token) - size + 1))
    return grams


def _scope_key(scope: MemoryScope) -> str:
    if scope.kind == "global":
        return "global"
    if scope.kind == "role":
        return f"role:{scope.role or ''}"
    return f"packet:{scope.packet_id or ''}"


def _entry_ref(entry: MemoryEntry) -> str:
    payload = _entry_identity_json(entry)
    return f"mem:{_digest(payload)}"


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _entry_identity_json(entry: MemoryEntry) -> dict[str, object]:
    return {
        "schema_version": 1,
        "scope": _scope_to_json(entry.scope),
        "text": entry.text,
        "created_at": entry.created_at,
    }


def _entry_to_json(entry: MemoryEntry) -> dict[str, object]:
    return {
        **_entry_identity_json(entry),
        "ref": entry.ref,
        "level": entry.level,
        "hits": entry.hits,
        "helpful": entry.helpful,
    }


def _entry_from_json(value: dict[str, object]) -> MemoryEntry:
    return MemoryEntry(
        ref=_string(value, "ref"),
        level=cast(MemoryLevel, _string(value, "level")),
        scope=_scope_from_json(_object(value, "scope")),
        text=_string(value, "text"),
        created_at=_string(value, "created_at"),
        hits=_int(value, "hits"),
        helpful=_int(value, "helpful"),
    )


def _scope_to_json(scope: MemoryScope) -> dict[str, object]:
    return {
        "kind": scope.kind,
        "role": scope.role,
        "packet_id": scope.packet_id,
    }


def _scope_from_json(value: dict[str, object]) -> MemoryScope:
    kind = _string(value, "kind")
    if kind == "global":
        return MemoryScope(kind="global")
    if kind == "role":
        return MemoryScope(kind="role", role=cast(Any, value.get("role")))
    if kind == "packet":
        return MemoryScope(kind="packet", packet_id=cast(Any, value.get("packet_id")))
    raise MemoryIndexError(f"invalid memory scope kind: {kind!r}")


def _justification_to_json(value: PromotionJustification) -> dict[str, object]:
    return {"kind": value.kind, "detail": value.detail, "refs": list(value.refs)}


def _read_json_object(path: Path) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MemoryIndexError(f"cannot read memory entry: {path}") from exc
    if not isinstance(raw, dict):
        raise MemoryIndexError(f"memory entry must be a JSON object: {path}")
    return raw


def _object(value: dict[str, object], key: str) -> dict[str, object]:
    raw = value.get(key)
    if not isinstance(raw, dict):
        raise MemoryIndexError(f"memory field {key!r} must be an object")
    return raw


def _string(value: dict[str, object], key: str) -> str:
    raw = value.get(key)
    if not isinstance(raw, str) or not raw:
        raise MemoryIndexError(f"memory field {key!r} must be a non-empty string")
    return raw


def _int(value: dict[str, object], key: str) -> int:
    raw = value.get(key)
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
        raise MemoryIndexError(f"memory field {key!r} must be a non-negative integer")
    return raw
