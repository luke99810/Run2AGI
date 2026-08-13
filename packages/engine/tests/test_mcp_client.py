"""MCP 客户端判据 —— 用**真实子进程**验证，不用 mock。

★ 为什么不 mock：这一层的价值就在于「能不能跟一个真的 MCP server 说上话」。
  mock 掉传输之后，剩下的只是在测我自己写的 JSON 序列化 ——
  而真实故障发生在握手、通知穿插、错误语义这些地方。

测试用的 server 是本目录下现写的一个最小 MCP 实现（标准库、几十行），
它遵守协议：initialize → initialized → tools/list → tools/call。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from codentum_engine.mcp_client import (
    MCP_PROTOCOL_VERSION,
    McpServerConfig,
    McpSession,
    load_mcp_configs,
)

# ══════════════════════════════════════════════════════════════
#  一个真实的最小 MCP server（作为子进程运行）
# ══════════════════════════════════════════════════════════════

_FAKE_SERVER = '''
import json, sys

def send(obj):
    sys.stdout.write(json.dumps(obj) + "\\n")
    sys.stdout.flush()

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        # ★ 故意先发一条通知，再发响应 —— 客户端必须跳过通知
        send({"jsonrpc": "2.0", "method": "notifications/message",
              "params": {"level": "info", "data": "starting"}})
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake-server", "version": "9.9.9"}}})
    elif method == "notifications/initialized":
        pass
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [
            {"name": "create_issue", "description": "创建一个 issue",
             "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}},
                             "required": ["title"]}},
            {"name": "always_fails", "description": "总是失败",
             "inputSchema": {"type": "object"}}]}})
    elif method == "tools/call":
        name = msg["params"]["name"]
        if name == "always_fails":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "isError": True,
                "content": [{"type": "text", "text": "配额不足"}]}})
        elif name == "unknown_tool":
            send({"jsonrpc": "2.0", "id": mid,
                  "error": {"code": -32602, "message": "Unknown tool"}})
        else:
            args = msg["params"].get("arguments", {})
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": "已创建：" + str(args.get("title"))}]}})
'''


@pytest.fixture
def server_script(tmp_path: Path) -> Path:
    script = tmp_path / "fake_mcp_server.py"
    script.write_text(_FAKE_SERVER, encoding="utf-8")
    return script


@pytest.fixture
def session(server_script: Path):  # type: ignore[no-untyped-def]
    config = McpServerConfig(
        id="github", name="GitHub", command=sys.executable, args=(str(server_script),)
    )
    s = McpSession(config)
    s.start()
    yield s
    s.close()


# ══════════════════════════════════════════════════════════════
#  握手
# ══════════════════════════════════════════════════════════════


def test_handshake_completes_against_a_real_process(session: McpSession) -> None:
    """★ 这一层的核心：能不能跟一个真的 MCP server 说上话。"""

    assert session.server_info.get("name") == "fake-server"
    assert session.protocol_version == MCP_PROTOCOL_VERSION


def test_notifications_before_the_response_are_skipped(session: McpSession) -> None:
    """★ 服务端可以在响应前先发通知（日志、进度）。

    把第一行当成响应是最容易犯的错 —— 测试用的 server 故意先发一条通知。
    握手能成功即证明跳过逻辑生效。
    """

    assert session.server_info != {}


# ══════════════════════════════════════════════════════════════
#  工具
# ══════════════════════════════════════════════════════════════


def test_tools_are_namespaced_by_server(session: McpSession) -> None:
    """★ 工具名必须带 server 前缀。

    不同 server 可能提供同名工具（GitHub 与 GitLab 都有 create_issue）。
    不加前缀会静默覆盖，而模型看到的还是那一个名字 ——
    **它不会知道自己调的是哪一个。**
    """

    tools = session.list_tools()
    names = {t.schema.name for t in tools}
    assert "github__create_issue" in names
    assert all(t.schema.name.startswith("github__") for t in tools)


def test_tool_schema_is_preserved(session: McpSession) -> None:
    """★ inputSchema 必须原样传给模型，否则它不知道怎么填参数。"""

    tool = next(t for t in session.list_tools() if t.tool_name == "create_issue")
    assert tool.schema.input_schema["required"] == ["title"]
    assert tool.schema.description == "创建一个 issue"


def test_successful_call_returns_content(session: McpSession) -> None:
    ok, content = session.call_tool("create_issue", {"title": "修复登录"})
    assert ok is True
    assert "已创建：修复登录" in content


def test_tool_level_error_is_not_reported_as_success(session: McpSession) -> None:
    """★ MCP 用 `isError` 表示**工具层面**的失败（协议本身是成功的）。

    把它当成功返回，模型会以为操作生效了并继续往下走 ——
    这正是本项目一路在拆的那个病，只是换了个位置。
    """

    ok, content = session.call_tool("always_fails", {})
    assert ok is False
    assert "配额不足" in content


def test_protocol_error_raises_with_a_usable_message(session: McpSession) -> None:
    """★ 协议级错误（-32602）要抛出且带上服务端的原话。"""

    with pytest.raises(RuntimeError, match="Unknown tool"):
        session.call_tool("unknown_tool", {})


# ══════════════════════════════════════════════════════════════
#  配置加载
# ══════════════════════════════════════════════════════════════


def test_declarative_manifests_are_skipped_not_fatal(tmp_path: Path) -> None:
    """★ 声明式清单（transport=http、只列工具名）与可执行配置同目录共存。

    前者不该让整个加载失败 —— 它们的作用是在界面上显示
    「已声明但未接入」，那本身是有价值的信息。
    """

    (tmp_path / "agentteams.json").write_text(
        json.dumps({"id": "agentteams", "transport": "http", "tools": ["dispatch_task"]}),
        encoding="utf-8",
    )
    (tmp_path / "github.json").write_text(
        json.dumps({"id": "github", "transport": "stdio", "command": "npx", "args": ["-y", "x"]}),
        encoding="utf-8",
    )

    configs = load_mcp_configs(tmp_path)
    assert [c.id for c in configs] == ["github"]


def test_disabled_servers_are_not_loaded(tmp_path: Path) -> None:
    (tmp_path / "x.json").write_text(
        json.dumps({"id": "x", "transport": "stdio", "command": "echo", "enabled": False}),
        encoding="utf-8",
    )
    assert load_mcp_configs(tmp_path) == ()


def test_malformed_config_does_not_break_the_others(tmp_path: Path) -> None:
    """★ 一个坏配置不该让所有 MCP 都不可用。"""

    (tmp_path / "broken.json").write_text("{ 这不是 JSON", encoding="utf-8")
    (tmp_path / "good.json").write_text(
        json.dumps({"id": "good", "transport": "stdio", "command": "echo"}), encoding="utf-8"
    )

    configs = load_mcp_configs(tmp_path)
    assert [c.id for c in configs] == ["good"]


# ══════════════════════════════════════════════════════════════
#  可移植性
# ══════════════════════════════════════════════════════════════


def test_command_is_resolved_through_path(tmp_path: Path, server_script: Path) -> None:
    """★ 命令必须经 PATH 解析后再交给 CreateProcess。

    Windows 上 `npx` / `npm` 是 .cmd 批处理，`shell=False` 直接传名字会
    WinError 2 —— 而所有第三方 MCP server（GitHub / 飞书 / 支付宝）
    的推荐启动方式正是 `npx`。

    表现为「配置写对了但连不上」，且**只在一个平台上出现**。
    修复不能改用 shell=True —— 那会引入命令注入面。
    """

    import shutil

    # 用不带路径的解释器名（python / python3），验证它能被解析
    bare = "python" if shutil.which("python") else "python3"
    if shutil.which(bare) is None:
        pytest.skip("环境中没有可用于验证 PATH 解析的裸命令名")

    session = McpSession(
        McpServerConfig(id="portable", name="Portable", command=bare, args=(str(server_script),))
    )
    session.start()
    try:
        assert session.server_info.get("name") == "fake-server"
    finally:
        session.close()


def test_non_ascii_content_survives_the_pipe(session: McpSession) -> None:
    """★ 中文必须原样穿过 stdio 管道。

    子进程若用系统默认编码（中文 Windows 上是 GBK）写 stdout，
    父进程按 UTF-8 解码会得到乱码 —— 而 **JSON 结构本身是 ASCII，
    解析不报错**，只有内容里的非 ASCII 字符坏掉。
    这类故障不会让任何协议层断言变红，只能靠内容断言抓住。
    """

    ok, content = session.call_tool("create_issue", {"title": "修复登录问题"})
    assert ok is True
    assert "修复登录问题" in content, f"编码在管道上坏了：{content!r}"
