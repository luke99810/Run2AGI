from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

DELIVERY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DELIVERY_ROOT))

from codentum_delivery.gateway import SidecarGateway
from codentum_delivery.protocol import CAPABILITY_NAMES, parse_request


def command(command_id: str = "cmd-1", *, delay_ms: int = 0) -> dict[str, object]:
    return {
        "commandId": command_id,
        "runId": "run-1",
        "expectedRevision": 7,
        "target": {"agentId": "coder-1", "moduleId": "implementation"},
        "action": "stop",
        "payload": {"testDelayMs": delay_ms},
        "requestedAt": "2026-08-07T12:00:00.000Z",
    }


class SidecarGatewayTests(unittest.TestCase):
    def test_no_engine_is_explicitly_fail_closed(self) -> None:
        gateway = SidecarGateway(None)
        response = gateway.dispatch(
            parse_request({"id": "h", "method": "handshake", "params": {"protocolVersion": 1}})
        )
        result = response["result"]
        self.assertIsInstance(result, dict)
        assert isinstance(result, dict)
        self.assertFalse(result["connected"])
        self.assertEqual(set(result["capabilities"]), set(CAPABILITY_NAMES))
        self.assertFalse(any(result["capabilities"].values()))

        receipt_response = gateway.dispatch(
            parse_request({"id": "c", "method": "command", "params": {"command": command()}})
        )
        receipt = receipt_response["result"]
        self.assertEqual(receipt["status"], "rejected")
        self.assertEqual(receipt["reason"], "engine_unavailable")

    def test_real_engine_proxy_is_capability_gated_and_idempotent(self) -> None:
        fake_engine = Path(__file__).with_name("_fake_engine.py")
        gateway = SidecarGateway([sys.executable, "-u", str(fake_engine)], engine_timeout_seconds=3)
        handshake = gateway.start()
        self.assertTrue(handshake["connected"])
        self.assertEqual(handshake["runId"], "run-1")
        self.assertTrue(handshake["capabilities"]["stop"])
        deadline = time.monotonic() + 1
        while gateway.engine_stderr_bytes_consumed == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertGreater(gateway.engine_stderr_bytes_consumed, 0)

        first = gateway.dispatch(
            parse_request({"id": "c1", "method": "command", "params": {"command": command()}})
        )["result"]
        second = gateway.dispatch(
            parse_request({"id": "c2", "method": "command", "params": {"command": command()}})
        )["result"]
        self.assertEqual(first, second)
        self.assertEqual(first["stateRevision"], 8)
        refreshed = gateway.start()
        self.assertEqual(refreshed["stateRevision"], 8)

        changed = command()
        changed["payload"] = {"different": True}
        conflict = gateway.dispatch(
            parse_request({"id": "c3", "method": "command", "params": {"command": changed}})
        )["result"]
        self.assertEqual(conflict["status"], "rejected")
        self.assertEqual(conflict["reason"], "idempotency_conflict")
        self.assertTrue(gateway.close())

    def test_rejects_a_command_for_another_run(self) -> None:
        fake_engine = Path(__file__).with_name("_fake_engine.py")
        gateway = SidecarGateway([sys.executable, "-u", str(fake_engine)], engine_timeout_seconds=3)
        self.assertEqual(gateway.start()["runId"], "run-1")
        mismatched = command("cmd-wrong-run")
        mismatched["runId"] = "fixture:mid-flight"
        receipt = gateway.dispatch(
            parse_request({"id": "wrong-run", "method": "command", "params": {"command": mismatched}})
        )["result"]
        self.assertEqual(receipt["status"], "rejected")
        self.assertEqual(receipt["reason"], "run_mismatch")
        gateway.close()

    def test_timeout_returns_a_cached_rejection_and_never_retries(self) -> None:
        fake_engine = Path(__file__).with_name("_fake_engine.py")
        gateway = SidecarGateway([sys.executable, "-u", str(fake_engine)], engine_timeout_seconds=0.5)
        self.assertTrue(gateway.start()["connected"])
        delayed = command("cmd-timeout", delay_ms=750)
        first = gateway.dispatch(
            parse_request({"id": "t1", "method": "command", "params": {"command": delayed}})
        )["result"]
        second = gateway.dispatch(
            parse_request({"id": "t2", "method": "command", "params": {"command": delayed}})
        )["result"]
        self.assertEqual(first, second)
        self.assertEqual(first["reason"], "engine_timeout_reconcile_authoritative_state")
        gateway.close()

    def test_rejects_a_receipt_that_regresses_state_revision(self) -> None:
        fake_engine = Path(__file__).with_name("_fake_engine.py")
        gateway = SidecarGateway([sys.executable, "-u", str(fake_engine)], engine_timeout_seconds=3)
        self.assertEqual(gateway.start()["stateRevision"], 7)
        regressing = command("cmd-regress")
        regressing["payload"] = {"testRegressRevision": True}
        receipt = gateway.dispatch(
            parse_request({"id": "regress", "method": "command", "params": {"command": regressing}})
        )["result"]
        self.assertEqual(receipt["status"], "rejected")
        self.assertEqual(receipt["reason"], "non_monotonic_state_revision")
        self.assertEqual(receipt["stateRevision"], 7)
        gateway.close()

    def test_stdio_server_emits_only_jsonl_and_shuts_down(self) -> None:
        sidecar = DELIVERY_ROOT / "codentum_delivery" / "sidecar.py"
        environment = dict(os.environ)
        environment.pop("CODENTUM_ENGINE_COMMAND_JSON", None)
        process = subprocess.Popen(
            [sys.executable, "-u", str(sidecar)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        assert process.stdin is not None and process.stdout is not None
        handshake_request = {"id": "h", "method": "handshake", "params": {"protocolVersion": 1}}
        process.stdin.write(json.dumps(handshake_request) + "\n")
        process.stdin.flush()
        handshake = json.loads(process.stdout.readline())
        self.assertTrue(handshake["ok"])
        self.assertFalse(handshake["result"]["connected"])
        process.stdin.write(json.dumps({"id": "s", "method": "shutdown", "params": {}}) + "\n")
        process.stdin.flush()
        shutdown = json.loads(process.stdout.readline())
        self.assertTrue(shutdown["ok"])
        self.assertEqual(process.wait(timeout=3), 0)
        self.assertEqual(process.stdout.read(), "")
        process.stdin.close()
        process.stdout.close()
        assert process.stderr is not None
        process.stderr.close()


if __name__ == "__main__":
    unittest.main()
