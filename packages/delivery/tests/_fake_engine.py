#!/usr/bin/env python
"""Protocol-v1 fake used only to test transport; never shipped as an engine."""

from __future__ import annotations

import json
import sys
import time

CAPABILITIES = {
    "requirements": True,
    "planConfirmation": True,
    "pauseAtSafePoint": True,
    "resume": True,
    "stop": True,
    "keepMemory": True,
    "forkFromCheckpoint": True,
    "appendPrompt": True,
    "insertModule": True,
}


def respond(request_id: str, result: object) -> None:
    sys.stdout.write(json.dumps({"id": request_id, "ok": True, "result": result}) + "\n")
    sys.stdout.flush()


revision = 7
for line in sys.stdin:
    request = json.loads(line)
    method = request["method"]
    if method == "handshake":
        # More than a typical pipe buffer: a proxy that does not consume stderr deadlocks here.
        sys.stderr.write("diagnostic-line\n" * 16384)
        sys.stderr.flush()
        respond(
            request["id"],
            {
                "connected": True,
                "protocolVersion": 1,
                "engineVersion": "fake-test-engine",
                "stateRevision": revision,
                "runId": "run-1",
                "capabilities": CAPABILITIES,
            },
        )
    elif method == "command":
        command = request["params"]["command"]
        delay_ms = command.get("payload", {}).get("testDelayMs", 0)
        if delay_ms:
            time.sleep(delay_ms / 1000)
        if command.get("payload", {}).get("testRegressRevision"):
            response_revision = revision - 1
        else:
            revision += 1
            response_revision = revision
        respond(
            request["id"],
            {
                "commandId": command["commandId"],
                "status": "applied",
                "stateRevision": response_revision,
                "receivedAt": "2026-08-07T12:00:00.000Z",
            },
        )
    elif method == "shutdown":
        respond(request["id"], {"stopped": True})
        raise SystemExit(0)
    else:
        sys.stdout.write(
            json.dumps(
                {
                    "id": request["id"],
                    "ok": False,
                    "error": {"code": "method_not_found", "message": "unsupported"},
                }
            )
            + "\n"
        )
        sys.stdout.flush()
