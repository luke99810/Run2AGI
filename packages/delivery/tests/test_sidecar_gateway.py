from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

DELIVERY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DELIVERY_ROOT))

from codentum_delivery.gateway import SidecarGateway
from codentum_delivery.protocol import CAPABILITY_NAMES, JsonValue, parse_request
from codentum_delivery.sidecar import bundled_engine_command


def _obj(value: JsonValue) -> dict[str, JsonValue]:
    """把 JsonValue 收窄成对象，收窄不了就当场失败。

    ★ 不是为了让 mypy 闭嘴：协议响应的 `result` 按契约必须是对象，
      收窄失败本身就是一条值得测试红掉的信息 —— 比 `# type: ignore`
      之后在下一行拿到 TypeError 要早，也要具体。
    """

    assert isinstance(value, dict), f"期望 JSON 对象，实际是 {type(value).__name__}"
    return value


def command(command_id: str = "cmd-1", *, delay_ms: int = 0) -> dict[str, object]:
    return {
        "commandId": command_id,
        "runId": "run-1",
        "expectedRevision": 7,
        "target": {"agentId": "coder-1", "moduleId": "implementation"},
        "action": "stop",
        "payload": {"testDelayMs": delay_ms, "projectRoot": os.path.realpath(os.getcwd())},
        "requestedAt": "2026-08-07T12:00:00.000Z",
    }


class SidecarGatewayTests(unittest.TestCase):
    def test_bundled_sidecar_discovers_adjacent_engine_only_for_a_bound_project(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sidecar = root / "python" / "codentum-sidecar" / "codentum-sidecar.exe"
            engine = root / "python" / "codentum-engine" / (
                "codentum-engine.exe" if os.name == "nt" else "codentum-engine"
            )
            project = root / "project"
            sidecar.parent.mkdir(parents=True)
            engine.parent.mkdir(parents=True)
            project.mkdir()
            sidecar.touch()
            engine.touch()

            self.assertIsNone(bundled_engine_command(sidecar, None))
            self.assertEqual(
                bundled_engine_command(sidecar, project),
                [str(engine.resolve()), "--project-root", str(project.resolve())],
            )

    def test_no_engine_is_explicitly_fail_closed(self) -> None:
        gateway = SidecarGateway(None)
        response = gateway.dispatch(
            parse_request({"id": "h", "method": "handshake", "params": {"protocolVersion": 1}})
        )
        result = _obj(response["result"])
        self.assertIsInstance(result, dict)
        assert isinstance(result, dict)
        self.assertFalse(result["connected"])
        self.assertEqual(set(_obj(result["capabilities"])), set(CAPABILITY_NAMES))
        self.assertFalse(any(_obj(result["capabilities"]).values()))

        receipt_response = gateway.dispatch(
            parse_request({"id": "c", "method": "command", "params": {"command": command()}})
        )
        receipt = _obj(receipt_response["result"])
        self.assertEqual(receipt["status"], "rejected")
        self.assertEqual(receipt["reason"], "engine_unavailable")

    def test_real_engine_proxy_is_capability_gated_and_idempotent(self) -> None:
        fake_engine = Path(__file__).with_name("_fake_engine.py")
        gateway = SidecarGateway([sys.executable, "-u", str(fake_engine)], engine_timeout_seconds=3)
        handshake = gateway.start()
        self.assertTrue(handshake["connected"])
        self.assertEqual(handshake["runId"], "run-1")
        self.assertTrue(_obj(handshake["capabilities"])["stop"])
        deadline = time.monotonic() + 1
        while gateway.engine_stderr_bytes_consumed == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertGreater(gateway.engine_stderr_bytes_consumed, 0)

        first = _obj(gateway.dispatch(
            parse_request({"id": "c1", "method": "command", "params": {"command": command()}})
        )["result"])
        second = _obj(gateway.dispatch(
            parse_request({"id": "c2", "method": "command", "params": {"command": command()}})
        )["result"])
        self.assertEqual(first, second)
        self.assertEqual(first["stateRevision"], 8)
        refreshed = gateway.start()
        self.assertEqual(refreshed["stateRevision"], 8)

        changed = command()
        changed["payload"] = {"different": True}
        conflict = _obj(gateway.dispatch(
            parse_request({"id": "c3", "method": "command", "params": {"command": changed}})
        )["result"])
        self.assertEqual(conflict["status"], "rejected")
        self.assertEqual(conflict["reason"], "idempotency_conflict")
        self.assertTrue(gateway.close())

    def test_rejects_a_command_for_another_run(self) -> None:
        fake_engine = Path(__file__).with_name("_fake_engine.py")
        gateway = SidecarGateway([sys.executable, "-u", str(fake_engine)], engine_timeout_seconds=3)
        self.assertEqual(gateway.start()["runId"], "run-1")
        mismatched = command("cmd-wrong-run")
        mismatched["runId"] = "fixture:mid-flight"
        receipt = _obj(gateway.dispatch(
            parse_request({"id": "wrong-run", "method": "command", "params": {"command": mismatched}})
        )["result"])
        self.assertEqual(receipt["status"], "rejected")
        self.assertEqual(receipt["reason"], "run_mismatch")
        gateway.close()

    def test_rejects_a_command_for_another_project(self) -> None:
        fake_engine = Path(__file__).with_name("_fake_engine.py")
        gateway = SidecarGateway([sys.executable, "-u", str(fake_engine)], engine_timeout_seconds=3)
        self.assertTrue(gateway.start()["connected"])
        mismatched = command("cmd-wrong-project")
        mismatched["payload"] = {"projectRoot": str(Path(os.getcwd()).parent)}
        receipt = _obj(gateway.dispatch(
            parse_request({"id": "wrong-project", "method": "command", "params": {"command": mismatched}})
        )["result"])
        self.assertEqual(receipt["status"], "rejected")
        self.assertEqual(receipt["reason"], "project_mismatch")
        gateway.close()

    def test_timeout_returns_a_cached_rejection_and_never_retries(self) -> None:
        fake_engine = Path(__file__).with_name("_fake_engine.py")
        gateway = SidecarGateway([sys.executable, "-u", str(fake_engine)], engine_timeout_seconds=0.5)
        self.assertTrue(gateway.start()["connected"])
        delayed = command("cmd-timeout", delay_ms=750)
        first = _obj(gateway.dispatch(
            parse_request({"id": "t1", "method": "command", "params": {"command": delayed}})
        )["result"])
        second = _obj(gateway.dispatch(
            parse_request({"id": "t2", "method": "command", "params": {"command": delayed}})
        )["result"])
        self.assertEqual(first, second)
        self.assertEqual(first["reason"], "engine_timeout_reconcile_authoritative_state")
        gateway.close()

    def test_rejects_a_receipt_that_regresses_state_revision(self) -> None:
        fake_engine = Path(__file__).with_name("_fake_engine.py")
        gateway = SidecarGateway([sys.executable, "-u", str(fake_engine)], engine_timeout_seconds=3)
        self.assertEqual(gateway.start()["stateRevision"], 7)
        regressing = command("cmd-regress")
        regressing["payload"] = {
            "testRegressRevision": True,
            "projectRoot": os.path.realpath(os.getcwd()),
        }
        receipt = _obj(gateway.dispatch(
            parse_request({"id": "regress", "method": "command", "params": {"command": regressing}})
        )["result"])
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
