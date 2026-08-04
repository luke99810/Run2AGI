"""Minimal JSON Lines sidecar used to validate Electron ↔ Python transport.

This is deliberately a dependency-free development probe.  It is not the
production engine and it is not a PyInstaller artifact.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import time
from collections.abc import Mapping
from typing import Any

PROTOCOL_VERSION = 1


def _configure_stdio() -> None:
    """Keep the JSON Lines wire format UTF-8 on every Windows locale."""
    sys.stdin.reconfigure(encoding="utf-8", errors="strict")
    sys.stdout.reconfigure(encoding="utf-8", errors="strict", newline="\n")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace", newline="\n")


def _write_json(payload: Mapping[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _write_error(message: str) -> None:
    sys.stderr.write(f"[python-engine-probe] {message}\n")
    sys.stderr.flush()


def _error_response(request_id: str, code: str, message: str) -> dict[str, Any]:
    return {
        "id": request_id,
        "ok": False,
        "error": {"code": code, "message": message},
    }


def _handle_request(request: object) -> tuple[dict[str, Any], bool]:
    if not isinstance(request, dict):
        return _error_response("unknown", "invalid_request", "request must be an object"), False

    request_id = request.get("id")
    method = request.get("method")
    if not isinstance(request_id, str) or not request_id:
        return _error_response("unknown", "invalid_request", "id must be a non-empty string"), False
    if not isinstance(method, str) or not method:
        return _error_response(request_id, "invalid_request", "method must be a non-empty string"), False

    if method == "ping":
        return (
            {
                "id": request_id,
                "ok": True,
                "result": {
                    "kind": "pong",
                    "protocolVersion": PROTOCOL_VERSION,
                    "pythonVersion": platform.python_version(),
                    "pid": os.getpid(),
                },
            },
            False,
        )

    if method == "probe.delay":
        params = request.get("params", {})
        if not isinstance(params, dict):
            return _error_response(request_id, "invalid_params", "params must be an object"), False
        delay_ms = params.get("delayMs", 0)
        if not isinstance(delay_ms, int) or isinstance(delay_ms, bool) or not 0 <= delay_ms <= 10_000:
            return _error_response(request_id, "invalid_params", "delayMs must be between 0 and 10000"), False
        if params.get("emitStderr") is True:
            _write_error("delay diagnostic")
        time.sleep(delay_ms / 1_000)
        return {"id": request_id, "ok": True, "result": {"delayedMs": delay_ms}}, False

    if method == "probe.echo":
        params = request.get("params", {})
        if not isinstance(params, dict):
            return _error_response(request_id, "invalid_params", "params must be an object"), False
        return {"id": request_id, "ok": True, "result": {"value": params.get("value")}}, False

    if method == "shutdown":
        return {"id": request_id, "ok": True, "result": {"kind": "bye"}}, True

    return _error_response(request_id, "method_not_found", f"unknown method: {method}"), False


def main() -> int:
    _configure_stdio()
    if sys.version_info < (3, 11):
        _write_error("Python 3.11 or newer is required")
        return 64

    _write_error(f"ready on Python {platform.python_version()}")
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            request: object = json.loads(line)
        except json.JSONDecodeError:
            response, should_exit = _error_response(
                "unknown", "invalid_json", "input must be one JSON object per line"
            ), False
        else:
            try:
                response, should_exit = _handle_request(request)
            except Exception as error:  # noqa: BLE001 - sidecar must preserve the transport on bad input
                _write_error(f"internal error: {type(error).__name__}")
                response, should_exit = _error_response(
                    request.get("id", "unknown") if isinstance(request, dict) else "unknown",
                    "internal_error",
                    "request failed",
                ), False

        try:
            _write_json(response)
        except BrokenPipeError:
            return 0
        if should_exit:
            return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
