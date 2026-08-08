"""Versioned JSONL protocol shared by the delivery gateway and its tests.

Stdout is reserved for one JSON response per input line.  This module deliberately
has no dependency on the control plane so the bundled sidecar remains startable even
when the real A/B engine has not been supplied.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Final, TypeAlias

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

PROTOCOL_VERSION: Final = 1
GATEWAY_VERSION: Final = "0.1.0"

CAPABILITY_NAMES: Final[tuple[str, ...]] = (
    "requirements",
    "planConfirmation",
    "pauseAtSafePoint",
    "resume",
    "stop",
    "keepMemory",
    "forkFromCheckpoint",
    "appendPrompt",
    "insertModule",
)

ACTION_CAPABILITY: Final[dict[str, str]] = {
    "submit_requirement": "requirements",
    "confirm_plan": "planConfirmation",
    "pause_at_safe_point": "pauseAtSafePoint",
    "resume": "resume",
    "stop": "stop",
    "stop_keep_memory": "keepMemory",
    "fork_from_checkpoint": "forkFromCheckpoint",
    "append_prompt": "appendPrompt",
    "insert_module": "insertModule",
}

RECEIPT_STATUSES: Final = frozenset({"accepted", "waiting_safe_point", "applied", "rejected"})


class ProtocolViolation(ValueError):
    """An input or engine response does not satisfy protocol v1."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class Request:
    request_id: str
    method: str
    params: dict[str, JsonValue]


def empty_capabilities() -> dict[str, bool]:
    """Return a fresh all-false map; never share a mutable singleton."""

    return {name: False for name in CAPABILITY_NAMES}


def unavailable_handshake(reason: str) -> dict[str, JsonValue]:
    return {
        "connected": False,
        "protocolVersion": PROTOCOL_VERSION,
        "engineVersion": "unavailable",
        "stateRevision": 0,
        "capabilities": empty_capabilities(),
        "unavailableReason": reason,
    }


def parse_request(value: object) -> Request:
    if not isinstance(value, dict):
        raise ProtocolViolation("invalid_request", "request must be a JSON object")
    request_id = value.get("id")
    method = value.get("method")
    params = value.get("params", {})
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 256:
        raise ProtocolViolation("invalid_request_id", "id must be a non-empty string")
    if not isinstance(method, str) or not method.strip() or len(method) > 128:
        raise ProtocolViolation("invalid_method", "method must be a non-empty string")
    if not isinstance(params, dict) or not all(isinstance(key, str) for key in params):
        raise ProtocolViolation("invalid_params", "params must be a JSON object")
    return Request(request_id=request_id, method=method, params=params)


def validate_handshake(value: object) -> dict[str, JsonValue]:
    """Validate and normalize an external engine handshake.

    Capabilities are fail-closed: missing, extra, or non-boolean values are rejected.
    An engine declaring itself disconnected may not advertise an enabled capability.
    """

    if not isinstance(value, dict):
        raise ProtocolViolation("invalid_handshake", "engine handshake must be an object")
    if value.get("protocolVersion") != PROTOCOL_VERSION:
        raise ProtocolViolation("protocol_mismatch", f"engine must implement protocol {PROTOCOL_VERSION}")
    connected = value.get("connected")
    engine_version = value.get("engineVersion")
    revision = value.get("stateRevision")
    run_id = value.get("runId")
    project_root = value.get("projectRoot")
    raw_capabilities = value.get("capabilities")
    if not isinstance(connected, bool):
        raise ProtocolViolation("invalid_handshake", "connected must be boolean")
    if not isinstance(engine_version, str) or not engine_version or len(engine_version) > 128:
        raise ProtocolViolation("invalid_handshake", "engineVersion must be a non-empty string")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise ProtocolViolation("invalid_handshake", "stateRevision must be a non-negative integer")
    if connected and (not isinstance(run_id, str) or not run_id or len(run_id) > 256):
        raise ProtocolViolation("invalid_handshake", "a connected engine must provide a valid runId")
    if not connected and run_id is not None:
        raise ProtocolViolation("invalid_handshake", "a disconnected engine must not advertise runId")
    if connected and (
        not isinstance(project_root, str)
        or not project_root
        or len(project_root) > 4096
        or not os.path.isabs(project_root)
    ):
        raise ProtocolViolation("invalid_handshake", "a connected engine must provide an absolute projectRoot")
    if not connected and project_root is not None:
        raise ProtocolViolation("invalid_handshake", "a disconnected engine must not advertise projectRoot")
    if not isinstance(raw_capabilities, dict):
        raise ProtocolViolation("invalid_handshake", "capabilities must be an object")
    if set(raw_capabilities) != set(CAPABILITY_NAMES):
        raise ProtocolViolation("invalid_capabilities", "engine must declare every protocol-v1 capability")
    if not all(isinstance(raw_capabilities[name], bool) for name in CAPABILITY_NAMES):
        raise ProtocolViolation("invalid_capabilities", "capability values must be boolean")
    capabilities = {name: bool(raw_capabilities[name]) for name in CAPABILITY_NAMES}
    if not connected and any(capabilities.values()):
        raise ProtocolViolation("invalid_capabilities", "a disconnected engine cannot enable capabilities")

    normalized: dict[str, JsonValue] = {
        "connected": connected,
        "protocolVersion": PROTOCOL_VERSION,
        "engineVersion": engine_version,
        "stateRevision": revision,
        "capabilities": capabilities,
    }
    if connected:
        assert isinstance(run_id, str)
        assert isinstance(project_root, str)
        normalized["runId"] = run_id
        normalized["projectRoot"] = os.path.realpath(project_root)
    reason = value.get("unavailableReason")
    if reason is not None:
        if not isinstance(reason, str) or not reason or len(reason) > 512:
            raise ProtocolViolation("invalid_handshake", "unavailableReason must be a non-empty string")
        normalized["unavailableReason"] = reason
    return normalized


def validate_receipt(value: object, command_id: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ProtocolViolation("invalid_engine_receipt", "engine receipt must be an object")
    if value.get("commandId") != command_id:
        raise ProtocolViolation("invalid_engine_receipt", "engine receipt commandId does not match")
    status = value.get("status")
    revision = value.get("stateRevision")
    received_at = value.get("receivedAt")
    if not isinstance(status, str) or status not in RECEIPT_STATUSES:
        raise ProtocolViolation("invalid_engine_receipt", "engine receipt status is invalid")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise ProtocolViolation("invalid_engine_receipt", "engine receipt stateRevision is invalid")
    if not isinstance(received_at, str) or not is_timestamp(received_at):
        raise ProtocolViolation("invalid_engine_receipt", "engine receipt receivedAt is invalid")
    normalized: dict[str, JsonValue] = {
        "commandId": command_id,
        "status": status,
        "stateRevision": revision,
        "receivedAt": received_at,
    }
    reason = value.get("reason")
    if reason is not None:
        if not isinstance(reason, str) or len(reason) > 512:
            raise ProtocolViolation("invalid_engine_receipt", "engine receipt reason must be a string")
        normalized["reason"] = reason
    return normalized


def success_response(request_id: str, result: JsonValue) -> dict[str, JsonValue]:
    return {"id": request_id, "ok": True, "result": result}


def error_response(request_id: str, code: str, message: str) -> dict[str, JsonValue]:
    return {"id": request_id, "ok": False, "error": {"code": code, "message": message}}


def is_timestamp(value: str) -> bool:
    """Accept an RFC-3339-like timestamp only when it includes a timezone."""

    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return False
    return parsed.tzinfo is not None
