#!/usr/bin/env python
"""Codentum JSONL sidecar entry point.

The process never prints diagnostics to stdout while serving.  Every input line gets
exactly one response envelope.  A real engine is opt-in through a shell-free JSON argv
environment variable; no configured engine means all capabilities remain false.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import IO

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from codentum_delivery.engine_proxy import command_from_environment
from codentum_delivery.gateway import SidecarGateway
from codentum_delivery.protocol import (
    CAPABILITY_NAMES,
    PROTOCOL_VERSION,
    ProtocolViolation,
    error_response,
    parse_request,
)

MAX_REQUEST_CHARACTERS = 2 * 1024 * 1024


def bundled_engine_command(
    executable: Path | None = None,
    project_root: Path | None = None,
) -> list[str] | None:
    """Return the adjacent packaged engine argv without invoking a shell.

    Development remains explicit through ``CODENTUM_ENGINE_COMMAND_JSON``. A
    frozen sidecar may use the engine shipped beside it, but only after the
    desktop has bound a real project. This avoids creating runtime state in the
    installation directory during application startup.
    """

    if executable is None:
        if not getattr(sys, "frozen", False):
            return None
        executable = Path(sys.executable)
    if project_root is None:
        encoded_root = os.environ.get("CODENTUM_PROJECT_ROOT", "").strip()
        if not encoded_root:
            return None
        project_root = Path(encoded_root)
    resolved_root = project_root.resolve()
    if not resolved_root.is_dir():
        return None
    binary_name = "codentum-engine.exe" if os.name == "nt" else "codentum-engine"
    candidate = executable.resolve().parent.parent / "codentum-engine" / binary_name
    if not candidate.is_file():
        return None
    return [str(candidate), "--project-root", str(resolved_root)]


def _positive_float_env(name: str, default: float) -> float:
    encoded = os.environ.get(name)
    if encoded is None:
        return default
    try:
        value = float(encoded)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive number") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive number")
    return value


def _positive_int_env(name: str, default: int) -> int:
    encoded = os.environ.get(name)
    if encoded is None:
        return default
    try:
        value = int(encoded)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def gateway_from_environment() -> SidecarGateway:
    try:
        command = command_from_environment() or bundled_engine_command()
        timeout = _positive_float_env("CODENTUM_ENGINE_TIMEOUT_SECONDS", 8.0)
        cache_limit = _positive_int_env("CODENTUM_IDEMPOTENCY_CACHE_SIZE", 2048)
    except ValueError:
        return SidecarGateway(None, unavailable_reason="engine environment configuration is invalid")
    return SidecarGateway(command, engine_timeout_seconds=timeout, idempotency_limit=cache_limit)


def serve(stdin: IO[str], stdout: IO[str], gateway: SidecarGateway) -> int:
    try:
        while True:
            line = stdin.readline(MAX_REQUEST_CHARACTERS + 1)
            if line == "":
                break
            if len(line) > MAX_REQUEST_CHARACTERS:
                while line and not line.endswith("\n"):
                    line = stdin.readline(MAX_REQUEST_CHARACTERS + 1)
                response = error_response(
                    "protocol-error",
                    "request_too_large",
                    "request exceeds the 2 MiB limit",
                )
                stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
                stdout.flush()
                continue
            fallback_id = "protocol-error"
            try:
                decoded: object = json.loads(line)
                if (
                    isinstance(decoded, dict)
                    and isinstance(decoded.get("id"), str)
                    and len(decoded["id"]) <= 256
                ):
                    fallback_id = decoded["id"]
                request = parse_request(decoded)
                response = gateway.dispatch(request)
            except json.JSONDecodeError:
                response = error_response(fallback_id, "invalid_json", "request is not valid JSON")
            except ProtocolViolation as exc:
                response = error_response(fallback_id, exc.code, str(exc))
            stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            stdout.flush()
            if gateway.shutdown_requested:
                break
    except KeyboardInterrupt:
        return 130
    finally:
        gateway.close()
    return 0


def self_test() -> int:
    """Dependency-free gateway truthfulness and protocol smoke test."""

    gateway = SidecarGateway(None)
    handshake_response = gateway.dispatch(
        parse_request({"id": "self-handshake", "method": "handshake", "params": {"protocolVersion": 1}})
    )
    result = handshake_response.get("result")
    if handshake_response.get("ok") is not True or not isinstance(result, dict):
        return 1
    capabilities = result.get("capabilities")
    if (
        result.get("connected") is not False
        or not isinstance(capabilities, dict)
        or set(capabilities) != set(CAPABILITY_NAMES)
        or any(value is not False for value in capabilities.values())
    ):
        return 1
    shutdown = gateway.dispatch(
        parse_request({"id": "self-shutdown", "method": "shutdown", "params": {}})
    )
    if shutdown.get("ok") is not True:
        return 1
    print(f"sidecar self-test passed (protocol {PROTOCOL_VERSION}; engine unavailable is fail-closed)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Codentum protocol-v1 JSONL sidecar")
    parser.add_argument("--self-test", action="store_true", help="run a dependency-free smoke test and exit")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    return serve(sys.stdin, sys.stdout, gateway_from_environment())


if __name__ == "__main__":
    raise SystemExit(main())
