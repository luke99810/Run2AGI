"""真实的 MCP 客户端 —— 让第三方应用的工具真的能被 Agent 调用。

════════════════════════════════════════════════════════════════
 ★ 在这之前，MCP 只是一份声明式清单
════════════════════════════════════════════════════════════════

`packages/roles/mcp/*.json` 里写着 `"tools": ["create_worker", "dispatch_task"]`，
引擎把它投影到 `.codentum/mcp/`，桌面端把它显示出来 ——
**全程没有任何代码去连接、去列举、去调用**。

那份清单诚实地标了 `"status": "disconnected"`，所以它不算撒谎；
但「接入飞书 / GitHub / 支付宝」这件事，缺的正是这一层。

════════════════════════════════════════════════════════════════
 ★ 为什么第三方应用应该走 MCP，而不是各写一个适配器
════════════════════════════════════════════════════════════════

GitHub、飞书、支付宝各有官方或社区的 MCP server。走 MCP 意味着：

- **主 Agent 接一次**，所有已连服务的工具自动进入工具面
- 新增一个第三方应用 = 加一条配置，**不改任何代码**
- 鉴权、重试、错误语义由各自的 server 负责，不污染主循环

这与项目既有的分层一致：Skill 是能力抽象，MCP 是工具连接。

════════════════════════════════════════════════════════════════
 ★ 实现约束
════════════════════════════════════════════════════════════════

- **零第三方依赖**：JSON-RPC over stdio 用标准库就够，不引入 mcp SDK
- **失败不阻断**：某个 server 连不上时，其余工具照常可用，
  并**如实记录哪个没连上** —— 不静默降级成「没有这个工具」
- **协议版本协商**：服务端返回不同版本时按规范接受其版本
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codentum_contracts.interfaces import ToolSchema

__all__ = [
    "MCP_PROTOCOL_VERSION",
    "McpServerConfig",
    "McpSession",
    "McpToolRef",
    "load_mcp_configs",
]

logger = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2025-06-18"
"""客户端支持的协议版本。服务端若返回不同版本，按规范接受服务端的版本。"""

_REQUEST_TIMEOUT_SECONDS = 30.0
_STARTUP_TIMEOUT_SECONDS = 20.0


@dataclass(frozen=True, slots=True)
class McpServerConfig:
    """一个 MCP server 的启动配置。

    ★ 只支持 stdio 传输。HTTP 传输需要额外的鉴权头与会话管理，
      而当前所有目标（GitHub / 飞书 / 文件系统 / Git）都提供 stdio server。
      **不实现的传输方式不在配置里出现**，避免「配了但不生效」。
    """

    id: str
    name: str
    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    requires_env: tuple[str, ...] = ()
    """启动前必须存在的环境变量（凭据）。

    ★ 这个字段以前只写在 JSON 里、没有任何代码读它 —— 于是缺凭据时
      子进程照样启动，然后以一个看不懂的错误失败（或更糟：静默返回空工具）。
      **使用者会以为是配置写错了，而实际只是没设 token。**
    """

    @staticmethod
    def from_json(raw: dict[str, Any]) -> McpServerConfig | None:
        """从配置文件解析。**只认 stdio 且带 command 的条目。**

        ★ 返回 None 而不是抛错：声明式清单（transport=http、只列工具名）
          与可执行配置放在同一目录，前者不该让整个加载失败。
        """

        if raw.get("transport") != "stdio":
            return None
        command = raw.get("command")
        if not isinstance(command, str) or not command.strip():
            return None
        return McpServerConfig(
            id=str(raw.get("id") or raw.get("name") or command),
            name=str(raw.get("name") or raw.get("id") or command),
            command=command,
            args=tuple(str(a) for a in raw.get("args", ())),
            env={str(k): str(v) for k, v in (raw.get("env") or {}).items()},
            enabled=bool(raw.get("enabled", True)),
            requires_env=tuple(str(k) for k in raw.get("requiresEnv", ())),
        )

    def missing_env(self) -> tuple[str, ...]:
        """返回缺失的凭据变量名。

        ★ 同时看进程环境与配置里的 env —— 使用者可以二选一：
          写进本机环境变量，或直接填在配置的 env 字段里。
        """

        return tuple(
            name
            for name in self.requires_env
            if not (self.env.get(name) or os.environ.get(name))
        )


@dataclass(frozen=True, slots=True)
class McpToolRef:
    """一个来自 MCP server 的工具。

    ★ `qualified_name` 带 server 前缀，因为不同 server 可能提供同名工具
      （GitHub 与 GitLab 都有 `create_issue`）。不加前缀会静默覆盖，
      而模型看到的还是那一个名字 —— 它不会知道自己调的是哪一个。
    """

    server_id: str
    tool_name: str
    schema: ToolSchema

    @property
    def qualified_name(self) -> str:
        return f"{self.server_id}__{self.tool_name}"


class McpSession:
    """与单个 MCP server 的 stdio 会话。

    生命周期：`start()` → `list_tools()` → `call_tool()` … → `close()`
    """

    def __init__(self, config: McpServerConfig) -> None:
        self._config = config
        self._proc: subprocess.Popen[str] | None = None
        self._next_id = 0
        self._lock = threading.Lock()
        self.server_info: dict[str, Any] = {}
        self.protocol_version: str = ""

    # ── 生命周期 ────────────────────────────────────────────

    def start(self) -> None:
        """启动子进程并完成 initialize 握手。"""

        env = dict(os.environ)
        # ★ 强制子进程用 UTF-8 读写标准流。
        #
        #   不设这一条时，子进程（尤其 Python/Node）在中文 Windows 上会用
        #   系统默认编码（GBK）写 stdout，父进程按 UTF-8 解码得到乱码 ——
        #   而 JSON 结构本身是 ASCII，**解析不报错**，只有内容里的非 ASCII
        #   字符坏掉。这类故障不会让任何协议层断言变红。
        #
        #   本项目已在流编码上踩过一次，这是第二次。
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")
        env.update(self._config.env)
        # ★ Windows 上 `npx` / `npm` 是 .cmd 批处理，`shell=False` 的
        #   CreateProcess 找不到它们（WinError 2）。用 shutil.which 解析成
        #   真实可执行路径 —— **不能改用 shell=True**，那会引入命令注入面。
        #
        #   这类缺陷只在一个平台上出现，且表现为「配置写对了但连不上」，
        #   本项目已在路径分隔符、流编码上踩过同类坑。
        executable = shutil.which(self._config.command) or self._config.command
        self._proc = subprocess.Popen(  # noqa: S603 - argv 明确，shell=False
            [executable, *self._config.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
            shell=False,
        )

        result = self._request(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "codentum-engine", "version": "0.1.0"},
            },
            timeout=_STARTUP_TIMEOUT_SECONDS,
        )
        # ★ 服务端可以返回它自己支持的版本，规范要求客户端接受或断开。
        #   我们接受 —— 断开的代价（整个 server 不可用）远大于版本差异。
        self.protocol_version = str(result.get("protocolVersion", MCP_PROTOCOL_VERSION))
        self.server_info = dict(result.get("serverInfo") or {})
        self._notify("notifications/initialized", {})

    def close(self) -> None:
        """按规范关闭：先关 stdin，等退出，超时才终止。"""

        proc = self._proc
        if proc is None:
            return
        self._proc = None
        try:
            if proc.stdin is not None:
                proc.stdin.close()
            proc.wait(timeout=5.0)
        except Exception:  # noqa: BLE001
            proc.kill()

    # ── 能力 ────────────────────────────────────────────────

    def list_tools(self) -> tuple[McpToolRef, ...]:
        result = self._request("tools/list", {})
        refs: list[McpToolRef] = []
        for item in result.get("tools") or ():
            name = item.get("name")
            if not isinstance(name, str) or not name:
                continue
            refs.append(
                McpToolRef(
                    server_id=self._config.id,
                    tool_name=name,
                    schema=ToolSchema(
                        name=f"{self._config.id}__{name}",
                        description=str(item.get("description") or name),
                        input_schema=item.get("inputSchema") or {"type": "object"},
                    ),
                )
            )
        return tuple(refs)

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        """调用工具。返回 (是否成功, 文本内容)。

        ★ MCP 用 `isError` 表示**工具层面**的失败（而非协议失败）。
          把它当成功返回会让模型以为操作生效了 —— 必须如实分开。
        """

        result = self._request("tools/call", {"name": tool_name, "arguments": arguments})
        is_error = bool(result.get("isError", False))
        chunks: list[str] = []
        for block in result.get("content") or ():
            if isinstance(block, dict) and block.get("type") == "text":
                chunks.append(str(block.get("text", "")))
            elif isinstance(block, dict):
                # 非文本块（图片、资源）不丢弃，如实标注类型
                chunks.append(f"[{block.get('type', 'unknown')} 内容，未展开]")
        return (not is_error), "\n".join(chunks) if chunks else "(无内容)"

    # ── JSON-RPC ────────────────────────────────────────────

    def _request(
        self, method: str, params: dict[str, Any], *, timeout: float = _REQUEST_TIMEOUT_SECONDS
    ) -> dict[str, Any]:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.stdout is None:
            raise RuntimeError(f"MCP server {self._config.id} 未启动")

        with self._lock:
            self._next_id += 1
            request_id = self._next_id
            payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
            proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            proc.stdin.flush()

            # ★ 顺序读取直到拿到匹配 id 的响应。
            #   服务端可能先发通知（日志、进度），必须跳过而不是当成响应。
            deadline_lines = 200
            for _ in range(deadline_lines):
                line = proc.stdout.readline()
                if not line:
                    raise RuntimeError(f"MCP server {self._config.id} 在响应前关闭了输出")
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue  # 非 JSON 行（某些 server 会打印横幅）直接跳过
                if message.get("id") != request_id:
                    continue
                if "error" in message:
                    err = message["error"]
                    raise RuntimeError(
                        f"MCP {self._config.id}.{method} 失败："
                        f"{err.get('code')} {err.get('message')}"
                    )
                return dict(message.get("result") or {})
            raise RuntimeError(f"MCP server {self._config.id} 在 {deadline_lines} 行内未返回响应")

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            return
        with self._lock:
            proc.stdin.write(
                json.dumps({"jsonrpc": "2.0", "method": method, "params": params}, ensure_ascii=False)
                + "\n"
            )
            proc.stdin.flush()


def load_mcp_configs(config_dir: Path | str) -> tuple[McpServerConfig, ...]:
    """加载目录下所有**可执行的** MCP 配置。

    ★ 声明式清单（transport != stdio 或没有 command）会被跳过，
      它们仍然可以被投影到界面上作为「已声明但未接入」——
      两者并存是有意的：**声明不等于可用，但也不该因为不可用就不显示**。
    """

    directory = Path(config_dir)
    if not directory.is_dir():
        return ()
    configs: list[McpServerConfig] = []
    for path in sorted(directory.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("MCP 配置 %s 解析失败，已跳过：%s", path.name, exc)
            continue
        config = McpServerConfig.from_json(raw)
        if config is not None and config.enabled:
            configs.append(config)
    return tuple(configs)
