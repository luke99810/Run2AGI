"""跨进程判据 —— 用 C 那边真正会用的 `SidecarGateway` 去驱动真引擎。

★ 这组测试的价值全在「不许用替身」：
  `packages/delivery/tests/test_sidecar_gateway.py` 用的是 `_fake_engine.py`，
  它证明的是**传输层**对。这里用的是真引擎二进制，证明的是**接线**对。

  08-10 的教训（§十七 / real-model-run）就是这条：替身不需要的东西，
  测试也测不出缺。假引擎不需要知道 runId 要持久化、不需要知道能力表该报
  什么、不需要真的建 packet —— 所以那些缺陷在它面前全是绿的。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from codentum_delivery.gateway import SidecarGateway
from codentum_delivery.protocol import JsonValue, Request

_KEY_ENVS = ("DASHSCOPE_API_KEY", "BAILIAN_API_KEY", "QWEN_API_KEY", "AGENTTEAMS_LLM_API_KEY")
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _obj(value: JsonValue) -> dict[str, JsonValue]:
    """把 JsonValue 收窄成对象，收窄不了就当场失败。

    ★ 协议响应按契约必须是对象，收窄失败本身就是值得测试红掉的信息。
    """

    assert isinstance(value, dict), f"期望 JSON 对象，实际是 {type(value).__name__}"
    return value


def _int(value: JsonValue) -> int:
    assert isinstance(value, int) and not isinstance(value, bool), (
        f"期望整数，实际是 {type(value).__name__}"
    )
    return value


def _text(value: JsonValue) -> str:
    assert isinstance(value, str), f"期望字符串，实际是 {type(value).__name__}"
    return value


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    return root


@pytest.fixture
def engine_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """让子进程能 import 到五个包，并给它一个假 Key。

    ★ 假 Key 是有意的：这组测试只走到「packet 被建出来」，不调用模型。
      真调模型的判据在 `tests/e2e/test_real_model_execution.py`，
      那条有 Key 才跑，没有就报「未执行（不是通过）」。
    """

    roots = [
        _REPO_ROOT / "packages" / "contracts" / "python",
        _REPO_ROOT / "packages" / "control-plane",
        _REPO_ROOT / "packages" / "harness",
        _REPO_ROOT / "packages" / "roles",
        _REPO_ROOT / "packages" / "delivery",
        _REPO_ROOT / "packages" / "engine",
    ]
    existing = os.environ.get("PYTHONPATH", "")
    joined = os.pathsep.join(str(r) for r in roots)
    monkeypatch.setenv("PYTHONPATH", f"{joined}{os.pathsep}{existing}" if existing else joined)
    for name in _KEY_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "REPLACE_ME_DASHSCOPE_API_KEY_FOR_TESTS")
    yield


def _engine_command(project: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "codentum_engine",
        "--project-root",
        str(project),
        "--log-level",
        "WARNING",
    ]


def _submit(
    gateway: SidecarGateway,
    handshake: dict[str, JsonValue],
    project: Path,
    text: str,
) -> dict[str, JsonValue]:
    return gateway.dispatch(
        Request(
            request_id="req-1",
            method="command",
            params={
                "command": {
                    "commandId": "cmd-1",
                    "runId": handshake["runId"],
                    "expectedRevision": handshake["stateRevision"],
                    "target": {"agentId": "operator"},
                    "action": "submit_requirement",
                    "payload": {"projectRoot": str(project), "requirement": text},
                    "requestedAt": "2026-08-10T12:00:00.000Z",
                }
            },
        )
    )


def test_gateway_completes_a_handshake_with_the_real_engine(
    project: Path, engine_env: None
) -> None:
    """★ 在这条测试之前，`SidecarGateway` 从未与一个真引擎握过手。

    它握过手的唯一对象是 `_fake_engine.py`，而那个文件第一行就写着
    "never shipped as an engine"。
    """

    gateway = SidecarGateway(_engine_command(project), engine_timeout_seconds=30.0)
    try:
        handshake = gateway.start()
        assert handshake["connected"] is True, handshake.get("unavailableReason")
        assert _text(handshake["engineVersion"]).startswith("codentum-engine/")
        assert _obj(handshake["capabilities"])["requirements"] is True
        # ★ 未实现的能力必须报 false，否则桌面端会把按钮显示为可用
        assert _obj(handshake["capabilities"])["forkFromCheckpoint"] is False
    finally:
        gateway.close()


def test_submitting_a_requirement_creates_a_real_packet_on_disk(
    project: Path, engine_env: None
) -> None:
    """★ 端到端的那一步：桌面端发一条命令 → 磁盘上真的多了一个 packet。

    这是「UI → IPC → 协议 → 网关 → 引擎 → ReconcileLoop → .codentum/」
    这条链路第一次被一条测试从头走到尾。
    """

    gateway = SidecarGateway(_engine_command(project), engine_timeout_seconds=30.0)
    try:
        handshake = gateway.start()
        assert handshake["connected"] is True
        response = _submit(gateway, handshake, project, "做一个可以管理订阅费用的软件")
        assert response["ok"] is True, response
        receipt = _obj(response["result"])
        # ★ accepted 而不是 applied —— 活还没干完，回执不能替结果说话
        assert receipt["status"] == "accepted", receipt.get("reason")
    finally:
        gateway.close()

    packets = sorted((project / ".codentum" / "packets").glob("*.json"))
    assert len(packets) == 1
    packet = json.loads(packets[0].read_text("utf-8"))
    assert packet["role"] == "coder"
    assert packet["routing"]["model"]

    requirements = sorted((project / ".codentum" / "requirements").glob("*.json"))
    assert len(requirements) == 1
    assert "订阅费用" in json.loads(requirements[0].read_text("utf-8"))["text"]


def test_state_directory_stays_coherent_for_the_desktop(project: Path, engine_env: None) -> None:
    """★ `.codentum/` 是 A 与 C 之间唯一的接口，形状由 fixtures/golden-state
    定义。少一个成员，C 的 `directory-state-source` 会把整份快照判为
    incoherent —— 而现象是桌面端一片空白，不是报错。"""

    gateway = SidecarGateway(_engine_command(project), engine_timeout_seconds=30.0)
    try:
        handshake = gateway.start()
        _submit(gateway, handshake, project, "做点什么")
    finally:
        gateway.close()

    state = project / ".codentum"
    for member in ("graph.json", "budget.json", "decisions.jsonl"):
        assert (state / member).exists(), f"缺 {member}，桌面端会判 incoherent"
    for directory in ("packets", "evidence", "knowledge"):
        assert (state / directory).is_dir(), f"缺 {directory}/，桌面端会判 incoherent"

    # graph.json 的 nodes 必须与 packets/ 一致，否则 C 会报 [partial-write]
    graph = json.loads((state / "graph.json").read_text("utf-8"))
    nodes = set(graph["dependency"]["nodes"])
    on_disk = {p.stem for p in (state / "packets").glob("*.json")}
    assert nodes == on_disk


def test_engine_survives_a_restart_without_regressing_the_revision(
    project: Path, engine_env: None
) -> None:
    """★ 网关判 `revision < 上一次` 为 non_monotonic_state_revision 并拒绝。

    假引擎的计数器活在进程里，重启归零。真引擎重启后必须接着往上走 ——
    否则重启之后桌面端发的第一条命令就会被自己的网关拒掉。
    """

    first = SidecarGateway(_engine_command(project), engine_timeout_seconds=30.0)
    try:
        handshake = first.start()
        run_id = handshake["runId"]
        _submit(first, handshake, project, "第一次提交")
    finally:
        first.close()

    second = SidecarGateway(_engine_command(project), engine_timeout_seconds=30.0)
    try:
        reopened = second.start()
        assert reopened["runId"] == run_id, "同一个 .codentum/ 就是同一次 run"
        assert _int(reopened["stateRevision"]) >= _int(handshake["stateRevision"]) + 1
    finally:
        second.close()


def test_stdout_carries_only_protocol_lines(project: Path, engine_env: None) -> None:
    """★ `JsonlEngineProxy` 按行 `json.loads` stdout。任何一句 print 都会被当成
    协议响应，解析失败后 `_fail_all` 把**所有**在途请求一起判错 ——
    现象是「引擎突然全线超时」，真因只是某处打了个日志。

    这条测试直接开子进程读裸 stdout，不经过代理。
    """

    proc = subprocess.Popen(
        [*_engine_command(project), "--log-level", "DEBUG"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=os.environ.copy(),
    )
    stdout, stderr = proc.communicate(
        json.dumps({"id": "r1", "method": "handshake", "params": {}}) + "\n"
        + json.dumps({"id": "r2", "method": "shutdown", "params": {}}) + "\n",
        timeout=60,
    )

    lines = [line for line in stdout.splitlines() if line.strip()]
    assert lines, "引擎没有任何输出"
    for line in lines:
        decoded = json.loads(line)  # 任何一行不是 JSON，这里就炸
        assert "id" in decoded
    # 日志确实产生了，只是走了 stderr —— 否则这条测试可能只是因为没日志才绿
    assert stderr.strip(), "没有任何 stderr 输出，说明日志根本没开，这条测试是空转的"
