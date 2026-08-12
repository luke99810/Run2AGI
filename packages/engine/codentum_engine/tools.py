"""worker 能真正执行的工具 —— 从「模型把代码写在回复里」到「文件真的落盘」。

════════════════════════════════════════════════════════════════
 ★ 在这之前，`RoleSpec.tools` 里的名字全是声明，没有实现
════════════════════════════════════════════════════════════════

`derive_tool_surface()` 按 RoleSpec 白名单派生出一份**可见工具清单**，
但 `ToolDescriptor` 在契约里只有描述，没有执行体 ——
全仓库没有任何工具注册表。于是：

  - 模型收到的 prompt 里 `Visible Tools: (none)`
  - `ModelGatewayRunner` 是 one-shot：发一次请求、把回复存成 response.txt 就结束
  - 模型只能把代码**写在回复正文里**，一个文件都不会被创建

2026-08-11 实测到的正是这个：`tool_calls` 为空、`touched_paths` 为空，
packet 被 `MUST_TOUCH_FILES_KINDS` 判据正确地拦在 review ——
**系统能诚实地说「没干成」，但它确实还干不成。**

这个模块补的就是「干得成」那一半。

════════════════════════════════════════════════════════════════
 ★ 三条安全边界（都不是可选的）
════════════════════════════════════════════════════════════════

1. **写入必须落在 workspace 内。** 路径穿越（`../`、绝对路径、符号链接）
   一律拒绝 —— 不是"警告后放行"，是拒绝。
   worker 拿到的是隔离工作区，越界写会污染真实仓库。

2. **只暴露 RoleSpec 白名单里的工具。** 未授权的工具**不出现在清单里**，
   而不是调用时才拒绝 —— 与 `derive_tool_surface` 同一条规矩：
   「无权限」先表现为「看不见」。

3. **每个工具都返回结构化结果，失败也返回。** 抛异常会中断工具循环，
   而模型本该有机会看到"这个工具失败了，原因是 X"并自己纠正。
   ★ 但失败必须**如实**返回，不能吞掉后返回成功 ——
   那会让模型以为写成功了，继续往下走。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codentum_contracts.interfaces import ToolSchema

__all__ = [
    "MAX_READ_CHARS",
    "MAX_WRITE_BYTES",
    "ToolExecutor",
    "ToolResult",
    "tool_schemas_for",
]

MAX_WRITE_BYTES = 256 * 1024
"""单个文件写入上限。★ 不是防御性数字：模型偶尔会把整个依赖库粘进来，
没有上限时一次调用就能把磁盘写满，而现象是"任务卡住"。"""

MAX_READ_CHARS = 40_000
"""单次读取返回给模型的字符上限。超出部分截断并明确告知 ——
**截断必须让模型知道**，否则它会以为自己看到了完整文件。"""

MAX_COMMAND_SECONDS = 120.0


@dataclass(frozen=True, slots=True)
class ToolResult:
    """一次工具调用的结果。**成功与失败用同一个形状返回。**"""

    ok: bool
    content: str

    def to_payload(self) -> dict[str, Any]:
        return {"ok": self.ok, "content": self.content}


_SCHEMAS: dict[str, ToolSchema] = {
    "write_file": ToolSchema(
        name="write_file",
        description=(
            "在工作区内创建或覆盖一个文本文件。path 必须是相对于工作区根的相对路径。"
            "这是唯一能让代码真正落盘的工具 —— 把代码写在回复正文里不算交付。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对于工作区根的路径，如 workspace/app.py"},
                "content": {"type": "string", "description": "文件完整内容"},
            },
            "required": ["path", "content"],
        },
    ),
    "read_file": ToolSchema(
        name="read_file",
        description="读取工作区内一个文本文件的内容。文件不存在时会如实告知，不要据此假设内容。",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    ),
    "list_files": ToolSchema(
        name="list_files",
        description="列出工作区内的文件。用它确认自己写出去的东西真的在。",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对目录，省略表示工作区根"}
            },
        },
    ),
    "run_tests": ToolSchema(
        name="run_tests",
        description=(
            "在工作区内运行一条命令并返回退出码与输出（如 python -m pytest）。"
            "用它验证自己写的代码能跑 —— 未经运行的代码不算完成。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "command": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "argv 形式，如 [\"python\", \"-m\", \"pytest\", \"-q\"]。不走 shell。",
                }
            },
            "required": ["command"],
        },
    ),
    "request_help": ToolSchema(
        name="request_help",
        description=(
            "当可见信息不足以继续时，明确说明缺什么。"
            "★ 用它比在正文里写「我做不了」更好：它会被结构化记录下来。"
        ),
        input_schema={
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
        },
    ),
}


def tool_schemas_for(tool_names: tuple[str, ...]) -> tuple[ToolSchema, ...]:
    """按 RoleSpec 的工具白名单挑出**已实现**的工具 schema。

    ★ 白名单里有、但这里没实现的工具会被静静跳过 —— 不报错，也不伪造。
      让模型看见一个点了没反应的工具，比让它看不见更糟
      （这与桌面端「未接入的不得显示为可用」是同一条规矩）。
    """

    return tuple(_SCHEMAS[name] for name in tool_names if name in _SCHEMAS)


class ToolExecutor:
    """在 worker 工作区内执行工具调用。**所有写入都被限制在工作区内。**"""

    def __init__(self, workspace: Path | str) -> None:
        self._root = Path(workspace).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self.written_paths: list[str] = []
        """本次执行真正写出去的文件（相对路径）。★ 这是"干没干活"的第一手证据。"""

    # ── 路径边界 ────────────────────────────────────────────

    def _resolve_inside(self, raw: str) -> Path:
        """把相对路径解析到工作区内，越界一律抛错。

        ★ 用 `resolve()` 之后再比较，是为了让 `../`、符号链接、
          `workspace/../../etc/passwd` 这类写法都在同一处被挡住 ——
          逐个模式匹配挡不干净，而漏掉一种就等于没挡。
        """

        candidate = (self._root / raw).resolve()
        if candidate != self._root and self._root not in candidate.parents:
            raise ValueError(f"路径越出工作区：{raw}")
        return candidate

    # ── 工具实现 ────────────────────────────────────────────

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """派发一次工具调用。**任何失败都返回 ToolResult(ok=False)，不抛异常。**

        ★ 抛异常会中断工具循环，而模型本该有机会看到"这个工具失败了，
          原因是 X"并自己纠正。但失败必须如实返回 —— 吞掉之后返回成功，
          会让模型以为写成功了继续往下走，那比直接失败更糟。
        """

        try:
            handler = getattr(self, f"_tool_{name}", None)
            if handler is None:
                return ToolResult(False, f"未知工具：{name}")
            return handler(arguments)  # type: ignore[no-any-return]
        except Exception as exc:  # noqa: BLE001 —— 见上方注释
            return ToolResult(False, f"{type(exc).__name__}: {exc}")

    def _tool_write_file(self, args: dict[str, Any]) -> ToolResult:
        raw_path = str(args.get("path", "")).strip()
        content = args.get("content")
        if not raw_path:
            return ToolResult(False, "path 不能为空")
        if not isinstance(content, str):
            return ToolResult(False, "content 必须是字符串")
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_WRITE_BYTES:
            return ToolResult(
                False, f"内容 {len(encoded)} 字节，超过上限 {MAX_WRITE_BYTES}；请拆分或精简"
            )

        target = self._resolve_inside(raw_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        relative = target.relative_to(self._root).as_posix()
        if relative not in self.written_paths:
            self.written_paths.append(relative)
        return ToolResult(True, f"已写入 {relative}（{len(encoded)} 字节）")

    def _tool_read_file(self, args: dict[str, Any]) -> ToolResult:
        target = self._resolve_inside(str(args.get("path", "")))
        if not target.is_file():
            return ToolResult(False, f"文件不存在：{args.get('path')}")
        text = target.read_text(encoding="utf-8", errors="replace")
        if len(text) > MAX_READ_CHARS:
            # ★ 截断必须说出来，否则模型会以为自己看到了完整文件
            return ToolResult(
                True, text[:MAX_READ_CHARS] + f"\n…（已截断，原文共 {len(text)} 字符）"
            )
        return ToolResult(True, text)

    def _tool_list_files(self, args: dict[str, Any]) -> ToolResult:
        base = self._resolve_inside(str(args.get("path", "") or "."))
        if not base.is_dir():
            return ToolResult(False, f"不是目录：{args.get('path')}")
        entries = [
            p.relative_to(self._root).as_posix()
            for p in sorted(base.rglob("*"))
            if p.is_file() and ".codentum" not in p.parts and ".git" not in p.parts
        ]
        return ToolResult(True, "\n".join(entries) if entries else "(工作区内没有文件)")

    def _tool_run_tests(self, args: dict[str, Any]) -> ToolResult:
        command = args.get("command")
        if not isinstance(command, list) or not command or not all(
            isinstance(part, str) and part for part in command
        ):
            return ToolResult(False, "command 必须是非空的字符串数组（argv 形式）")
        try:
            proc = subprocess.run(  # noqa: S603 - argv 明确，shell=False
                command,
                cwd=self._root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=MAX_COMMAND_SECONDS,
                # ★ 不继承 stdin：子进程若读 stdin 会挂住整个 worker。
                #   这个坑 08-11 已经在引擎入口踩过一次（git 卡死 240 秒）。
                stdin=subprocess.DEVNULL,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(False, f"命令超时（>{MAX_COMMAND_SECONDS:.0f} 秒）")
        except FileNotFoundError:
            return ToolResult(False, f"找不到可执行文件：{command[0]}")
        tail = (proc.stdout + proc.stderr)[-4000:]
        return ToolResult(
            proc.returncode == 0, f"退出码 {proc.returncode}\n{tail}"
        )

    def _tool_request_help(self, args: dict[str, Any]) -> ToolResult:
        reason = str(args.get("reason", "")).strip() or "(未说明原因)"
        return ToolResult(True, f"已记录求助：{reason}")

    # ── 证据 ────────────────────────────────────────────────

    def to_json(self) -> str:
        return json.dumps({"written_paths": self.written_paths}, ensure_ascii=False, indent=2)
