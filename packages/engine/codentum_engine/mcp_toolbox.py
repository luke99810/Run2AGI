"""MCP 工具箱 —— 把多个第三方服务的工具汇成一份可用清单。

★ 定位：**主 Agent 接一次，所有已连服务的工具自动进入工具面。**
  角色不需要各自配置 MCP；Skill 也不需要知道某个能力来自哪个 server。
  这与项目分层一致：Skill 是能力抽象，MCP 是工具连接。

★ 核心约束：**一个 server 连不上，不影响其余**。
  某个第三方服务挂了就让整个 Agent 失去所有工具，是把可用性绑在最弱的一环上。
  但失败必须**如实记录**，不能静默降级成「没有这个工具」——
  后者会让模型以为该能力从不存在，从而走完全错误的替代路径。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from codentum_contracts.interfaces import ToolSchema

from .mcp_client import McpServerConfig, McpSession, load_mcp_configs

if TYPE_CHECKING:
    from .tools import ToolResult

__all__ = ["McpToolbox", "build_mcp_toolbox"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class McpConnectionReport:
    """一次连接尝试的结果。★ 失败也要有记录，供界面与日志如实展示。"""

    server_id: str
    name: str
    connected: bool
    tool_count: int = 0
    error: str = ""


class McpToolbox:
    """已连接 MCP server 的集合，对外表现为一组可调用工具。"""

    def __init__(self) -> None:
        self._sessions: dict[str, McpSession] = {}
        self._tools: dict[str, tuple[str, str]] = {}
        """限定名 → (server_id, 原始工具名)"""
        self._schemas: list[ToolSchema] = []
        self.reports: list[McpConnectionReport] = []

    # ── 连接 ────────────────────────────────────────────────

    def connect_all(self, configs: tuple[McpServerConfig, ...]) -> None:
        """逐个连接。**单个失败不阻断其余。**"""

        for config in configs:
            try:
                session = McpSession(config)
                session.start()
                tools = session.list_tools()
            except Exception as exc:  # noqa: BLE001 —— 见模块注释
                logger.warning("MCP server %s 连接失败：%s", config.id, exc)
                self.reports.append(
                    McpConnectionReport(config.id, config.name, connected=False, error=str(exc))
                )
                continue

            self._sessions[config.id] = session
            for ref in tools:
                self._tools[ref.qualified_name] = (ref.server_id, ref.tool_name)
                self._schemas.append(ref.schema)
            self.reports.append(
                McpConnectionReport(config.id, config.name, connected=True, tool_count=len(tools))
            )
            logger.info("MCP server %s 已连接，提供 %d 个工具", config.id, len(tools))

    def close(self) -> None:
        for session in self._sessions.values():
            session.close()
        self._sessions.clear()

    # ── 工具面 ──────────────────────────────────────────────

    def schemas(self) -> tuple[ToolSchema, ...]:
        """所有已连接服务的工具 schema，可直接并入模型的工具面。"""

        return tuple(self._schemas)

    def owns(self, name: str) -> bool:
        return name in self._tools

    def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        from .tools import ToolResult  # 局部导入避免循环依赖

        entry = self._tools.get(name)
        if entry is None:
            return ToolResult(False, f"未知的 MCP 工具：{name}")
        server_id, tool_name = entry
        session = self._sessions.get(server_id)
        if session is None:
            return ToolResult(False, f"MCP server {server_id} 未连接")
        try:
            ok, content = session.call_tool(tool_name, arguments)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(False, f"MCP {server_id}.{tool_name} 调用失败：{exc}")
        return ToolResult(ok, content)

    # ── 可观测性 ────────────────────────────────────────────

    def summary(self) -> str:
        """人类可读的连接概况。★ 失败的也列出来，不只报好消息。"""

        if not self.reports:
            return "未配置任何可执行的 MCP server"
        lines: list[str] = []
        for report in self.reports:
            if report.connected:
                lines.append(f"  ✓ {report.name}（{report.server_id}）：{report.tool_count} 个工具")
            else:
                lines.append(f"  ✗ {report.name}（{report.server_id}）：{report.error[:120]}")
        return "\n".join(lines)


def build_mcp_toolbox(config_dir: Path | str) -> McpToolbox:
    """从配置目录构建并连接工具箱。

    ★ 即使一个都连不上也返回一个空工具箱，而不是 None ——
      调用方不必到处判空，且 `reports` 里仍有失败记录可查。
    """

    toolbox = McpToolbox()
    toolbox.connect_all(load_mcp_configs(config_dir))
    return toolbox
