"""A shell-free, timeout-bounded JSONL client for the real A/B engine."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, cast

from .protocol import JsonValue, ProtocolViolation

MAX_ENGINE_RESPONSE_CHARACTERS = 2 * 1024 * 1024


class EngineUnavailable(RuntimeError):
    pass


class EngineTimeout(TimeoutError):
    pass


class EngineRemoteError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(slots=True)
class _Pending:
    event: threading.Event = field(default_factory=threading.Event)
    response: object | None = None
    error: Exception | None = None


def command_from_environment(env: dict[str, str] | None = None) -> tuple[str, ...] | None:
    """Read a command as JSON argv, never as shell text.

    Example: ``CODENTUM_ENGINE_COMMAND_JSON=["engine.exe","--stdio"]``.
    """

    source = os.environ if env is None else env
    encoded = source.get("CODENTUM_ENGINE_COMMAND_JSON")
    if encoded is None or not encoded.strip():
        return None
    try:
        decoded = json.loads(encoded)
    except json.JSONDecodeError as exc:
        raise ValueError("CODENTUM_ENGINE_COMMAND_JSON must be a JSON string array") from exc
    if (
        not isinstance(decoded, list)
        or not decoded
        or not all(isinstance(item, str) and item for item in decoded)
    ):
        raise ValueError("CODENTUM_ENGINE_COMMAND_JSON must be a non-empty JSON string array")
    return tuple(decoded)


class JsonlEngineProxy:
    """Correlates JSONL requests while continuously draining both output streams."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        request_timeout_seconds: float = 8.0,
        cwd: Path | None = None,
    ) -> None:
        if not command or not all(isinstance(part, str) and part for part in command):
            raise ValueError("engine command must be a non-empty argv sequence")
        if request_timeout_seconds <= 0:
            raise ValueError("request timeout must be positive")
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            self._process = subprocess.Popen(  # noqa: S603 - argv is explicit and shell=False
                list(command),
                cwd=cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                shell=False,
                creationflags=creation_flags,
            )
        except (OSError, ValueError) as exc:
            raise EngineUnavailable("configured engine could not be started") from exc
        if self._process.stdin is None or self._process.stdout is None or self._process.stderr is None:
            self._process.kill()
            raise EngineUnavailable("configured engine did not expose stdio pipes")
        self._stdin: IO[str] = self._process.stdin
        self._stdout: IO[str] = self._process.stdout
        self._stderr: IO[str] = self._process.stderr
        self._timeout = request_timeout_seconds
        self._pending: dict[str, _Pending] = {}
        self._pending_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._closed = False
        self._stderr_bytes = 0
        self._stdout_thread = threading.Thread(target=self._read_stdout, name="engine-jsonl", daemon=True)
        self._stderr_thread = threading.Thread(target=self._drain_stderr, name="engine-stderr", daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

    @property
    def stderr_bytes_consumed(self) -> int:
        """Count bytes consumed without retaining potentially sensitive stderr text."""

        return self._stderr_bytes

    @property
    def is_running(self) -> bool:
        return not self._closed and self._process.poll() is None

    def request(
        self,
        method: str,
        params: dict[str, JsonValue],
        *,
        timeout_seconds: float | None = None,
    ) -> JsonValue:
        if not method:
            raise ValueError("method must not be empty")
        if not self.is_running:
            raise EngineUnavailable("engine process is not running")
        timeout = self._timeout if timeout_seconds is None else timeout_seconds
        if timeout <= 0:
            raise ValueError("request timeout must be positive")
        request_id = f"gateway-{uuid.uuid4()}"
        pending = _Pending()
        with self._pending_lock:
            self._pending[request_id] = pending
        encoded = json.dumps(
            {"id": request_id, "method": method, "params": params},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            with self._write_lock:
                self._stdin.write(encoded + "\n")
                self._stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise EngineUnavailable("engine stdin is unavailable") from exc
        if not pending.event.wait(timeout):
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise EngineTimeout(f"engine request {method!r} timed out")
        if pending.error is not None:
            raise pending.error
        response = pending.response
        if not isinstance(response, dict) or response.get("id") != request_id:
            raise ProtocolViolation("invalid_engine_response", "engine response envelope is invalid")
        ok = response.get("ok")
        if ok is True:
            return cast(JsonValue, response.get("result"))
        error = response.get("error")
        if ok is False and isinstance(error, dict):
            code = error.get("code")
            message = error.get("message")
            if isinstance(code, str) and isinstance(message, str):
                raise EngineRemoteError(code, message)
        raise ProtocolViolation("invalid_engine_response", "engine response envelope is invalid")

    def close(self, grace_seconds: float = 1.5) -> bool:
        if self._closed:
            return self._process.poll() is not None
        stopped_cleanly = False
        if self._process.poll() is None:
            try:
                self.request("shutdown", {}, timeout_seconds=max(0.05, grace_seconds))
            except (EngineUnavailable, EngineTimeout, EngineRemoteError, ProtocolViolation, ValueError):
                pass
            try:
                self._stdin.close()
            except OSError:
                pass
            try:
                self._process.wait(timeout=max(0.05, grace_seconds))
                stopped_cleanly = True
            except subprocess.TimeoutExpired:
                self._process.terminate()
                try:
                    self._process.wait(timeout=max(0.05, grace_seconds))
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=max(0.05, grace_seconds))
        else:
            stopped_cleanly = True
        self._closed = True
        self._fail_all(EngineUnavailable("engine process stopped"))
        self._stdout_thread.join(timeout=0.5)
        self._stderr_thread.join(timeout=0.5)
        for stream in (self._stdin, self._stdout, self._stderr):
            try:
                stream.close()
            except OSError:
                pass
        return stopped_cleanly

    def _read_stdout(self) -> None:
        try:
            while True:
                line = self._stdout.readline(MAX_ENGINE_RESPONSE_CHARACTERS + 1)
                if line == "":
                    break
                if len(line) > MAX_ENGINE_RESPONSE_CHARACTERS:
                    self._fail_all(
                        ProtocolViolation("engine_response_too_large", "engine response exceeds 2 MiB")
                    )
                    self._process.terminate()
                    return
                try:
                    decoded: object = json.loads(line)
                except json.JSONDecodeError:
                    self._fail_all(ProtocolViolation("invalid_engine_json", "engine emitted invalid JSON"))
                    continue
                request_id = decoded.get("id") if isinstance(decoded, dict) else None
                if not isinstance(request_id, str):
                    self._fail_all(
                        ProtocolViolation("invalid_engine_response", "engine response is missing a string id")
                    )
                    continue
                with self._pending_lock:
                    pending = self._pending.pop(request_id, None)
                if pending is not None:
                    pending.response = decoded
                    pending.event.set()
        except (OSError, ValueError):
            self._fail_all(EngineUnavailable("engine stdout failed"))
        finally:
            if not self._closed:
                self._fail_all(EngineUnavailable("engine exited before completing requests"))

    def _drain_stderr(self) -> None:
        try:
            for chunk in iter(lambda: self._stderr.read(4096), ""):
                self._stderr_bytes += len(chunk.encode("utf-8", errors="replace"))
        except (OSError, ValueError):
            return

    def _fail_all(self, error: Exception) -> None:
        with self._pending_lock:
            pending_values = list(self._pending.values())
            self._pending.clear()
        for pending in pending_values:
            pending.error = error
            pending.event.set()

    def __enter__(self) -> JsonlEngineProxy:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()
