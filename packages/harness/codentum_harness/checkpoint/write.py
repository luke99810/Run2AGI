"""Write deterministic worker checkpoints."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import TypeAlias, cast

from codentum_contracts.interfaces import CheckpointRef, SpawnRequest
from pydantic import BaseModel

from codentum_harness.context_broker import ContextBundle

__all__ = [
    "CheckpointWriteError",
    "write_initial_checkpoint",
]

JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


class CheckpointWriteError(ValueError):
    """A checkpoint cannot be serialized safely."""


def write_initial_checkpoint(
    *,
    worker_id: str,
    request: SpawnRequest,
    evidence_dir: Path | str,
    context: ContextBundle | None = None,
) -> CheckpointRef:
    """Write checkpoint-0 and return its stable content digest."""
    if not worker_id:
        raise CheckpointWriteError("worker_id must not be empty")

    payload: dict[str, JsonValue] = {
        "schema_version": 1,
        "kind": "input",
        "worker_id": worker_id,
        "seq": 0,
        "request": _to_jsonable(request),
        "context": _to_jsonable(context) if context is not None else None,
    }
    digest = _digest(payload)
    record = {**payload, "digest": digest}

    checkpoint_dir = Path(evidence_dir) / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    (checkpoint_dir / "0000.json").write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return CheckpointRef(worker_id=worker_id, seq=0, digest=digest)


def _digest(value: JsonValue) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _to_jsonable(value: object) -> JsonValue:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Enum):
        return _to_jsonable(value.value)
    if isinstance(value, BaseModel):
        return cast(
            JsonValue,
            value.model_dump(mode="json", by_alias=True, exclude_none=True),
        )
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _to_jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {_json_key(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_to_jsonable(item) for item in value]
    raise CheckpointWriteError(f"cannot serialize checkpoint value: {type(value).__name__}")


def _json_key(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Enum):
        return str(value.value)
    raise CheckpointWriteError(f"checkpoint mapping key must be a string: {type(value).__name__}")
