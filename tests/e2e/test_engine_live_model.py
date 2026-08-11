"""真实模型经**引擎入口**跑完一个 packet —— 整条链路最后一段判据。

════════════════════════════════════════════════════════════════
 它和 test_real_model_execution.py 的区别
════════════════════════════════════════════════════════════════

`test_real_model_execution.py` 证明的是：**控制平面能驱动真模型**。
它直接构造 `ReconcileLoop`，手写 packet，绕过了协议、网关和引擎进程。

这一条证明的是：**桌面端发一句人话，真模型跑完，磁盘上有产物**。
入口是 `SidecarGateway`（C 的桌面端用的就是它），出口是 `.codentum/`。

两条都要有。合并成一条会丢掉其中一个判据：
  - 只留上面那条 → 引擎入口坏了不会有人知道
  - 只留这一条 → 控制平面的问题会被埋在一长串管道里，定位不到

★ 没有 Key 时报「未执行（不是通过）」。
  一条被 skip 的测试和一条通过的测试，在报告里看起来太像了。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from codentum_delivery.gateway import SidecarGateway
from codentum_delivery.protocol import Request

_KEY_ENVS = ("DASHSCOPE_API_KEY", "BAILIAN_API_KEY", "QWEN_API_KEY", "AGENTTEAMS_LLM_API_KEY")
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _key_env() -> str | None:
    for name in _KEY_ENVS:
        if os.environ.get(name, "").strip():
            return name
    return None


requires_key = pytest.mark.skipif(
    _key_env() is None,
    reason=(
        "★ 未执行（不是通过）：本机没有百炼 Key。"
        f"配置 {' / '.join(_KEY_ENVS)} 任一后重跑。"
    ),
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "e2e@codentum.local"],
        ["git", "config", "user.name", "codentum-e2e"],
    ):
        subprocess.run(cmd, cwd=root, check=True, capture_output=True)
    (root / ".gitkeep").write_text("", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "e2e base"], cwd=root, check=True, capture_output=True)
    return root


def _engine_env() -> dict[str, str]:
    roots = [
        _REPO_ROOT / "packages" / "contracts" / "python",
        _REPO_ROOT / "packages" / "control-plane",
        _REPO_ROOT / "packages" / "harness",
        _REPO_ROOT / "packages" / "roles",
        _REPO_ROOT / "packages" / "delivery",
        _REPO_ROOT / "packages" / "engine",
    ]
    env = os.environ.copy()
    joined = os.pathsep.join(str(r) for r in roots)
    env["PYTHONPATH"] = f"{joined}{os.pathsep}{env['PYTHONPATH']}" if env.get("PYTHONPATH") else joined
    return env


@requires_key
def test_a_sentence_from_the_desktop_reaches_a_real_model(project: Path, monkeypatch) -> None:
    """★ 端到端：`SidecarGateway.dispatch` → 引擎进程 → ReconcileLoop → 真模型。"""

    for key, value in _engine_env().items():
        monkeypatch.setenv(key, value)

    command = [
        sys.executable,
        "-m",
        "codentum_engine",
        "--project-root",
        str(project),
        "--model-timeout-seconds",
        "180",
        "--log-level",
        "INFO",
    ]
    gateway = SidecarGateway(command, engine_timeout_seconds=30.0)
    try:
        handshake = gateway.start()
        assert handshake["connected"] is True, handshake.get("unavailableReason")
        assert handshake["capabilities"]["requirements"] is True, (
            "有 Key 却报 requirements=false —— 能力探测坏了"
        )

        response = gateway.dispatch(
            Request(
                request_id="req-live",
                method="command",
                params={
                    "command": {
                        "commandId": "cmd-live",
                        "runId": handshake["runId"],
                        "expectedRevision": handshake["stateRevision"],
                        "target": {"agentId": "operator"},
                        "action": "submit_requirement",
                        "payload": {
                            "projectRoot": str(project),
                            "requirement": (
                                "在 workspace/ 下新建 subscriptions.py，实现 Subscription "
                                "数据类（name / monthly_cny / renew_day）与 monthly_total(subs) "
                                "函数返回月度总额。只写这一个文件。"
                            ),
                        },
                        "requestedAt": "2026-08-11T00:00:00.000Z",
                    }
                },
            )
        )
        assert response["ok"] is True, response
        receipt = response["result"]
        # ★ accepted 不是 applied —— 活还没干完，回执不能替结果说话
        assert receipt["status"] == "accepted", receipt.get("reason")

        state = project / ".codentum"
        deadline = time.time() + 300
        packet: dict[str, object] = {}
        while time.time() < deadline:
            files = sorted((state / "packets").glob("*.json"))
            if files:
                packet = json.loads(files[0].read_text("utf-8"))
                if packet["state"] in {"accepted", "rejected", "abandoned"}:
                    break
            time.sleep(3)
    finally:
        gateway.close()

    assert packet, "没有任何 packet 落盘"
    assert packet["state"] == "accepted", f"终态是 {packet['state']}，evidence={packet['evidence']}"

    # ★ 验收依据必须是别人给的证据，不能是控制面自己的簿记。
    #   08-09 与 08-10 各修过这个洞一次（兜底分支 / 门禁分支）。
    real_evidence = [ref for ref in packet["evidence"] if not str(ref).startswith("sys:")]
    assert real_evidence, f"只有控制面簿记，没有真实证据：{packet['evidence']}"

    # 需求原文确实被存下来了 —— 契约里没有任务描述字段，靠的就是它
    requirements = sorted((state / "requirements").glob("*.json"))
    assert requirements, "需求原文没有落盘，模型收到的会是空上下文"
    assert "subscriptions.py" in json.loads(requirements[0].read_text("utf-8"))["text"]
