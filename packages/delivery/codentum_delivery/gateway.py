"""Capability-gated gateway between Electron and a separately owned real engine."""

from __future__ import annotations

import hashlib
import json
import os
from collections import OrderedDict
from collections.abc import Sequence
from datetime import UTC, datetime

from .engine_proxy import EngineRemoteError, EngineTimeout, EngineUnavailable, JsonlEngineProxy
from .protocol import (
    ACTION_CAPABILITY,
    GATEWAY_VERSION,
    PROTOCOL_VERSION,
    JsonValue,
    ProtocolViolation,
    Request,
    error_response,
    is_timestamp,
    success_response,
    unavailable_handshake,
    validate_handshake,
    validate_receipt,
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class SidecarGateway:
    """Owns one external engine process and a session-scoped idempotency cache.

    Durable idempotency remains an engine responsibility.  The gateway guarantees that
    a repeated command id is not re-sent during one sidecar process lifetime, including
    after a timeout where the engine's final outcome is unknown.
    """

    def __init__(
        self,
        engine_command: Sequence[str] | None,
        *,
        engine_timeout_seconds: float = 8.0,
        idempotency_limit: int = 2048,
        unavailable_reason: str | None = None,
    ) -> None:
        if engine_timeout_seconds <= 0:
            raise ValueError("engine timeout must be positive")
        if idempotency_limit < 1:
            raise ValueError("idempotency limit must be positive")
        self._engine_command = tuple(engine_command) if engine_command is not None else None
        self._engine_timeout = engine_timeout_seconds
        self._idempotency_limit = idempotency_limit
        self._engine: JsonlEngineProxy | None = None
        self._started = False
        self._shutdown_requested = False
        self._state_revision = 0
        self._unavailable_reason = unavailable_reason or "A/B engine command is not configured"
        self._handshake: dict[str, JsonValue] = unavailable_handshake(self._unavailable_reason)
        self._receipts: OrderedDict[str, tuple[str, dict[str, JsonValue]]] = OrderedDict()

    @property
    def shutdown_requested(self) -> bool:
        return self._shutdown_requested

    @property
    def engine_stderr_bytes_consumed(self) -> int:
        return 0 if self._engine is None else self._engine.stderr_bytes_consumed

    def start(self) -> dict[str, JsonValue]:
        self._started = True
        if self._engine_command is None:
            return dict(self._handshake)
        engine = self._engine
        try:
            if engine is None:
                engine = JsonlEngineProxy(
                    self._engine_command,
                    request_timeout_seconds=self._engine_timeout,
                )
                self._engine = engine
            raw = engine.request(
                "handshake",
                {"protocolVersion": PROTOCOL_VERSION, "gatewayVersion": GATEWAY_VERSION},
            )
            handshake = validate_handshake(raw)
            self._handshake = handshake
            revision = handshake["stateRevision"]
            self._state_revision = revision if isinstance(revision, int) else 0
            if handshake["connected"] is not True:
                reason = handshake.get("unavailableReason")
                self._mark_engine_unavailable(
                    reason if isinstance(reason, str) else "A/B engine reported unavailable"
                )
        except (EngineUnavailable, EngineTimeout, EngineRemoteError, ProtocolViolation, ValueError):
            self._mark_engine_unavailable("A/B engine handshake failed", engine)
        return dict(self._handshake)

    def _mark_engine_unavailable(
        self,
        reason: str,
        candidate: JsonlEngineProxy | None = None,
    ) -> None:
        engine = self._engine if self._engine is not None else candidate
        self._engine = None
        if engine is not None:
            engine.close()
        offline = unavailable_handshake(reason)
        offline["stateRevision"] = self._state_revision
        self._handshake = offline

    def dispatch(self, request: Request) -> dict[str, JsonValue]:
        try:
            result = self._invoke(request.method, request.params)
            return success_response(request.request_id, result)
        except ProtocolViolation as exc:
            return error_response(request.request_id, exc.code, str(exc))
        except Exception:
            # Inputs, environment values, and engine stderr are intentionally absent.
            return error_response(request.request_id, "internal_error", "sidecar request failed")

    def close(self) -> bool:
        self._shutdown_requested = True
        if self._engine is None:
            return True
        stopped = self._engine.close()
        self._engine = None
        return stopped

    def _invoke(self, method: str, params: dict[str, JsonValue]) -> JsonValue:
        if method == "handshake":
            requested = params.get("protocolVersion")
            if requested != PROTOCOL_VERSION:
                message = f"sidecar implements protocol {PROTOCOL_VERSION}"
                raise ProtocolViolation("protocol_mismatch", message)
            return self.start()
        if method == "command":
            return self._command(params)
        if method == "shutdown":
            stopped_cleanly = self.close()
            return {"stopped": True, "engineStoppedCleanly": stopped_cleanly}
        if method == "health":
            handshake = self.start()
            return {
                "gatewayVersion": GATEWAY_VERSION,
                "protocolVersion": PROTOCOL_VERSION,
                "engineConnected": handshake["connected"],
            }
        raise ProtocolViolation("method_not_found", "unsupported sidecar method")

    def _command(self, params: dict[str, JsonValue]) -> dict[str, JsonValue]:
        command = params.get("command")
        normalized = self._validate_command(command)
        command_id = normalized["commandId"]
        assert isinstance(command_id, str)
        fingerprint = hashlib.sha256(
            json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        cached = self._receipts.get(command_id)
        if cached is not None:
            cached_fingerprint, cached_receipt = cached
            self._receipts.move_to_end(command_id)
            if cached_fingerprint == fingerprint:
                return dict(cached_receipt)
            return self._rejected(command_id, "idempotency_conflict")
        if len(self._receipts) >= self._idempotency_limit:
            # Never evict an old command and risk replaying it later in this session.
            return self._rejected(command_id, "idempotency_capacity_exhausted_restart_required")

        action = normalized["action"]
        expected_revision = normalized["expectedRevision"]
        assert isinstance(action, str)
        assert isinstance(expected_revision, int)
        handshake = self.start()
        capabilities = handshake["capabilities"]
        capability = ACTION_CAPABILITY[action]
        if handshake["connected"] is not True or self._engine is None:
            receipt = self._rejected(command_id, "engine_unavailable")
        elif normalized["runId"] != handshake.get("runId"):
            receipt = self._rejected(command_id, "run_mismatch")
        elif not self._project_matches(normalized, handshake):
            receipt = self._rejected(command_id, "project_mismatch")
        elif not isinstance(capabilities, dict) or capabilities.get(capability) is not True:
            receipt = self._rejected(command_id, f"capability_unavailable:{capability}")
        elif expected_revision != self._state_revision:
            receipt = self._rejected(command_id, "stale_revision")
        else:
            receipt = self._forward_command(normalized, command_id)
        self._remember(command_id, fingerprint, receipt)
        return receipt

    @staticmethod
    def _project_matches(
        command: dict[str, JsonValue],
        handshake: dict[str, JsonValue],
    ) -> bool:
        payload = command.get("payload")
        expected = handshake.get("projectRoot")
        if not isinstance(payload, dict) or not isinstance(expected, str):
            return False
        actual = payload.get("projectRoot")
        if not isinstance(actual, str) or not os.path.isabs(actual):
            return False
        return os.path.normcase(os.path.realpath(actual)) == os.path.normcase(os.path.realpath(expected))

    def _forward_command(self, command: dict[str, JsonValue], command_id: str) -> dict[str, JsonValue]:
        assert self._engine is not None
        try:
            raw = self._engine.request(
                "command",
                {"command": command},
                timeout_seconds=self._engine_timeout,
            )
            receipt = validate_receipt(raw, command_id)
            revision = receipt["stateRevision"]
            if isinstance(revision, int):
                if revision < self._state_revision:
                    return self._rejected(command_id, "non_monotonic_state_revision")
                self._state_revision = revision
            return receipt
        except EngineTimeout:
            # Never retry automatically: the engine may have applied it after the deadline.
            return self._rejected(command_id, "engine_timeout_reconcile_authoritative_state")
        except EngineRemoteError:
            return self._rejected(command_id, "engine_error")
        except (EngineUnavailable, ProtocolViolation):
            self._mark_engine_unavailable("A/B engine protocol or transport failed")
            return self._rejected(command_id, "engine_protocol_or_transport_error")

    def _remember(self, command_id: str, fingerprint: str, receipt: dict[str, JsonValue]) -> None:
        self._receipts[command_id] = (fingerprint, dict(receipt))

    def _rejected(self, command_id: str, reason: str) -> dict[str, JsonValue]:
        return {
            "commandId": command_id,
            "status": "rejected",
            "stateRevision": self._state_revision,
            "receivedAt": _now(),
            "reason": reason,
        }

    @staticmethod
    def _validate_command(value: JsonValue | None) -> dict[str, JsonValue]:
        if not isinstance(value, dict):
            raise ProtocolViolation("invalid_command", "command must be an object")
        allowed_command = {
            "commandId",
            "runId",
            "expectedRevision",
            "target",
            "action",
            "payload",
            "requestedAt",
        }
        if set(value) - allowed_command:
            raise ProtocolViolation("invalid_command", "command contains unknown fields")
        command_id = value.get("commandId")
        run_id = value.get("runId")
        expected_revision = value.get("expectedRevision")
        target = value.get("target")
        action = value.get("action")
        payload = value.get("payload")
        requested_at = value.get("requestedAt")
        if not isinstance(command_id, str) or not command_id or len(command_id) > 256:
            raise ProtocolViolation("invalid_command", "commandId must be a non-empty string")
        if not isinstance(run_id, str) or not run_id or len(run_id) > 256:
            raise ProtocolViolation("invalid_command", "runId must be a non-empty string")
        if (
            not isinstance(expected_revision, int)
            or isinstance(expected_revision, bool)
            or expected_revision < 0
        ):
            raise ProtocolViolation("invalid_command", "expectedRevision must be a non-negative integer")
        if (
            not isinstance(target, dict)
            or not isinstance(target.get("agentId"), str)
            or not target["agentId"]
        ):
            raise ProtocolViolation("invalid_command", "target.agentId must be a non-empty string")
        if not isinstance(action, str) or action not in ACTION_CAPABILITY:
            raise ProtocolViolation("invalid_command", "action is not part of protocol v1")
        if not isinstance(payload, dict):
            raise ProtocolViolation("invalid_command", "payload must be an object")
        if not isinstance(requested_at, str) or not is_timestamp(requested_at):
            raise ProtocolViolation("invalid_command", "requestedAt must be a timezone-qualified timestamp")
        allowed_target = {"agentId", "packetId", "moduleId"}
        if set(target) - allowed_target:
            raise ProtocolViolation("invalid_command", "target contains unknown fields")
        for optional in ("packetId", "moduleId"):
            if optional in target and (not isinstance(target[optional], str) or not target[optional]):
                raise ProtocolViolation("invalid_command", f"target.{optional} must be a non-empty string")
        if any(isinstance(item, str) and len(item) > 256 for item in target.values()):
            raise ProtocolViolation("invalid_command", "target identifiers must not exceed 256 characters")
        return {
            "commandId": command_id,
            "runId": run_id,
            "expectedRevision": expected_revision,
            "target": dict(target),
            "action": action,
            "payload": dict(payload),
            "requestedAt": requested_at,
        }
