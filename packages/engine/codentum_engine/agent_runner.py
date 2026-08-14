"""带工具循环的 worker runner —— 让模型真的把代码写进文件。

════════════════════════════════════════════════════════════════
 ★ 它与 `ModelGatewayRunner` 的区别，以及为什么两个都要留着
════════════════════════════════════════════════════════════════

`ModelGatewayRunner`（B 写的）是 **one-shot**：发一次请求、把回复存成
`response.txt` 就结束。它证明的是「模型能被真实调用、用量能落盘」，
这一条判据本身是对的，不该删。

但它做不到「开发软件」：模型只能把代码**写在回复正文里**，
`tool_calls` 为空、工作区一个文件都没有。
2026-08-11 实测正是如此 —— packet 被 `MUST_TOUCH_FILES_KINDS` 判据
拦在 review，**系统能诚实地说「没干成」，但它确实还干不成。**

这个 runner 补的是「干得成」：

    invoke → 模型要求调工具 → 真的执行 → 结果回传 → 再 invoke → …
    直到模型说完事，或撞上轮数/预算上限。

★ 为什么放在 `packages/engine/` 而不是 harness：
  装配点本来就负责「装什么 runner」。而工具的执行边界
  （只能写工作区、不许路径穿越）是**产品决策**，不是执行外壳的实现细节。

★ 契约没有 `tool` 角色的消息（`ModelMessage.role` 只有 user/assistant），
  所以工具结果作为 **user 消息**回传。这不是将就 —— 契约已冻结，
  而这个形状足以让模型看懂"我刚才那次调用返回了什么"。
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from codentum_contracts.interfaces import (
    FailureCode,
    ModelGateway,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelSession,
    SpawnRequest,
    WorkerCompleted,
    WorkerFailed,
    WorkerOutcome,
)
from codentum_contracts.state import EvidenceRef
from codentum_harness.prompt_bundle import load_worker_prompt_bundle

from .acceptance import split_command, vacuity_check
from .agent_graph import AgentGraphState, build_agent_graph
from .mcp_toolbox import build_mcp_toolbox
from .tools import ToolExecutor, tool_schemas_for

__all__ = ["AgentRunnerConfig", "build_agent_runner"]

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")

DEFAULT_MAX_TURNS = 12
"""工具循环的轮数上限。

★ 这不是性能考虑，是**止损**：模型可能陷入"写文件→读文件→再写"的循环，
  而每一轮都在花钱。撞上限时如实报 `max_turns_exhausted`，
  **不伪装成完成** —— 那正是本项目追了四层的那个病。
"""


@dataclass(frozen=True, slots=True)
class AgentRunnerConfig:
    gateway: ModelGateway
    timeout_seconds: float = 600.0
    max_turns: int = DEFAULT_MAX_TURNS
    acceptance_predicate: str = "python -m pytest workspace -q"
    """会被门禁真的执行的那条谓词。写进 prompt 是为了让「完成」有唯一定义。"""

    mcp_config_dir: Path | None = None
    """MCP 配置目录。**主 Agent 接一次，所有已连服务的工具自动进入工具面。**

    ★ 为 None 时不连任何 MCP —— 内置工具照常可用，不影响主链路。
    """

    memory_dir: Path | None = None
    """进化层的记忆目录。为 None 时**不沉淀经验**。

    ★ 这里刻意不按 workspace 猜一个默认路径。`resolved_state_dir()` 是
      可以被显式覆盖的 —— 猜错就会分叉成两个记忆库，而两边各自看起来
      都正常工作，只是谁也攒不够晋级所需的次数。**静默地什么都学不到**
      比直接报错难查得多。

    ★ 没传 = 关闭，这件事会写进 result.json 的 `evolution` 字段，
      从外面看得见。否则「忘了接线」和「还没攒够」不可区分 ——
      那正是这个项目一路在拆的那类问题。
    """


def build_agent_runner(config: AgentRunnerConfig):  # type: ignore[no-untyped-def]
    """造一个 `WorkerRunner`（`Callable[[SpawnRequest], WorkerOutcome]`）。"""

    def run(req: SpawnRequest) -> WorkerOutcome:
        return _AgentRun(config, req, config.acceptance_predicate).execute()

    return run


class _AgentRun:
    def __init__(
        self, config: AgentRunnerConfig, req: SpawnRequest, acceptance_predicate: str
    ) -> None:
        self._config = config
        self._req = req
        self._workspace = Path(req.workspace)
        self._evidence_root = self._workspace / ".codentum" / "evidence" / (
            f"{req.packet_id}-attempt-{req.attempt}"
        )
        self._model_dir = self._evidence_root / "model"
        # ★ MCP 在这里接入：连不上的 server 不阻断内置工具。
        self._mcp = (
            build_mcp_toolbox(config.mcp_config_dir) if config.mcp_config_dir is not None else None
        )
        self._tools = ToolExecutor(self._workspace, mcp=self._mcp)
        self._transcript: list[dict[str, Any]] = []
        self._spent = 0.0
        self._acceptance_predicate = acceptance_predicate
        self._helped_once = False

    # ── 入口 ────────────────────────────────────────────────

    def execute(self) -> WorkerOutcome:
        try:
            return asyncio.run(
                asyncio.wait_for(self._run(), timeout=self._config.timeout_seconds)
            )
        except TimeoutError:
            return self._fail(
                FailureCode.TIMEOUT,
                f"工具循环超时（>{self._config.timeout_seconds:g}s）",
                status="timeout",
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("agent runner 失败")
            return self._fail(FailureCode.RUNTIME_ERROR, str(exc), status="failed")

    async def _run(self) -> WorkerOutcome:
        prompt = load_worker_prompt_bundle(self._evidence_root)
        tools = tool_schemas_for(tuple(self._req.tools))
        if self._mcp is not None:
            # ★ 第三方工具与内置工具并入同一个工具面。
            #   名字带 server 前缀，不会与内置工具冲突。
            mcp_schemas = self._mcp.schemas()
            if mcp_schemas:
                logger.info("已接入 MCP 工具 %d 个\n%s", len(mcp_schemas), self._mcp.summary())
            tools = tools + mcp_schemas
        if not tools:
            # ★ 没有可用工具时**不要静默退化成 one-shot** —— 那会让
            #   「模型没写文件」看起来像模型的问题，实际是配置问题。
            return self._fail(
                FailureCode.RUNTIME_ERROR,
                f"角色 {self._req.role} 没有任何已实现的工具（声明的是 {list(self._req.tools)}），"
                f"无法进行开发。请检查 RoleSpec.tools 与 codentum_engine.tools 的实现清单。",
                status="failed",
            )

        session: ModelSession | None = None
        messages: list[ModelMessage] = [
            ModelMessage(role="user", content=prompt.user + self._definition_of_done())
        ]

        try:
            session = await self._config.gateway.open(
                self._req.role, self._req.routing, self._req.budget.limit_cny
            )
            # ★ 按 ADR-0004：执行平面的控制流由 LangGraph 图驱动。
            #
            #   改造前是手写的 `for turn in range(...)` 循环 —— 它跑得通，
            #   但与 ADR-0004 的设计不符，而 langgraph 一直躺在依赖里、
            #   README 也写着「执行平面采用 LangGraph 编排」，实际零 import。
            #
            #   ★ 这是**行为等价重构**：`test_agent_runner.py` 的 13 条测试
            #     一条都没改。它们定义了必须保持的语义 ——
            #     能在不改测试的前提下换掉实现，才说明这是重构而非改行为。
            graph = build_agent_graph(
                model_node=self._make_model_node(session, prompt, tools),
                tools_node=self._tools_node,
                verify_node=self._verify_node,
                help_node=self._help_node,
            )

            final: AgentGraphState = await graph.ainvoke(
                {
                    "messages": list(messages),
                    "transcript": [],
                    "turn": 0,
                    "response": None,
                    "outcome": None,
                    "detail": "",
                    "helped_once": False,
                },
                # ★ 轮数上限交给图的递归上限执行。
                #   每轮最多经过 model → tools/verify/help 两个节点，
                #   留一倍余量避免图在到达业务上限前先被框架截断。
                {"recursion_limit": self._config.max_turns * 2 + 8},
            )

            outcome = final.get("outcome")
            if outcome == "help_requested":
                return self._fail(
                    FailureCode.ACCEPTANCE_NOT_MET, final["detail"], status="help_requested"
                )
            if outcome == "completed":
                return self._finish(prompt.digest, final.get("response"), final.get("turn", 0))
            # 图跑到头仍未收敛 = 撞上限
            return self._finish(prompt.digest, None, self._config.max_turns, exhausted=True)
        except Exception as exc:  # noqa: BLE001
            return self._fail(FailureCode.MODEL_ERROR, str(exc), status="failed")
        finally:
            if session is not None:
                await session.close()

    # ════════════════════════════════════════════════════════
    #  LangGraph 节点（ADR-0004：执行平面的控制流图）
    # ════════════════════════════════════════════════════════

    def _make_model_node(self, session: ModelSession, prompt: Any, tools: Any):  # type: ignore[no-untyped-def]
        """模型节点：发一次请求，把响应放进状态。

        ★ 截断恢复在这里：工具参数被截断时 JSON 不完整、救不回来，
          但模型自己能修 —— 告诉它「上次被截断了，写短一点」。
          直接判失败等于把一个可恢复的情况当成终局。
        """

        async def model_node(state: AgentGraphState) -> dict[str, Any]:
            turn = state.get("turn", 0) + 1
            if turn > self._config.max_turns:
                # ★ 撞上限不算完成 —— 模型没说「我做完了」，我们不能替它说。
                return {"turn": turn, "outcome": "max_turns_exhausted", "response": None}

            try:
                response = await self._invoke_with_retry(
                    session,
                    ModelRequest(
                        system=prompt.system,
                        messages=tuple(state["messages"]),
                        effort=self._req.routing.effort,
                        tools=tools,
                    ),
                )
            except ValueError as exc:
                if "JSON object text" not in str(exc) or turn >= self._config.max_turns:
                    raise
                logger.warning("工具调用参数被截断，回推让模型重写（第 %d 轮）", turn)
                return {
                    "turn": turn,
                    "response": None,
                    "messages": [
                        ModelMessage(role="assistant", content="（上一次工具调用输出被截断）"),
                        ModelMessage(
                            role="user",
                            content=(
                                "[系统] 你上一次的工具调用**参数被截断了**（单次输出超长）。"
                                "请重新调用，并把内容写短一些 —— "
                                "例如一次只写一个文件、去掉冗长注释。"
                            ),
                        ),
                    ],
                }

            self._spent = max(self._spent, _session_spent(session))
            self._record_turn(turn, response)
            return {"turn": turn, "response": response}

        return model_node

    def _tools_node(self, state: AgentGraphState) -> dict[str, Any]:
        """工具节点：执行本轮全部调用，结果作为 user 消息回传。

        ★ 不能发 content="" 的 assistant 消息 —— 模型只调工具不说话时
          `response.text` 是空的，而空 content 会被百炼判成畸形请求
          （实测连续两次 500，都在多轮的第 2–3 轮）。
        """

        response = state["response"]
        assert response is not None
        return {
            "messages": [
                ModelMessage(
                    role="assistant",
                    content=response.text
                    or "（调用工具：" + "、".join(c.name for c in response.tool_calls) + "）",
                ),
                ModelMessage(role="user", content=self._run_tool_calls(response)),
            ]
        }

    def _verify_node(self, state: AgentGraphState) -> dict[str, Any]:
        """自验节点：模型说完事了，但先把验收谓词跑一遍再放它走。

        ★ 验收谓词就是「完成」的定义。让模型在谓词不过时收尾，
          等于让它自己宣布达标 —— 那正是本项目一路在拆的那个病。
        """

        verdict = self._verify()
        if verdict is None or state.get("turn", 0) >= self._config.max_turns:
            return {"outcome": "completed"}

        response = state["response"]
        return {
            "messages": [
                ModelMessage(
                    role="assistant",
                    content=(response.text if response else None) or "（我认为已完成）",
                ),
                ModelMessage(role="user", content=verdict),
            ]
        }

    def _help_node(self, state: AgentGraphState) -> dict[str, Any]:
        """求助节点：首次给一次事实性回推，再次即终止。

        ★ 求助的语义是「我需要人来决定」，不该在求助后还让模型自己往下猜。
          但首次求助常常只是「不知道验收标准」，而验收标准一直在 prompt 里 ——
          把谓词的真实输出摆给它看，比重复一遍要求有效。
        """

        response = state["response"]
        assert response is not None
        reason = next(
            str(call.input.get("reason", "")).strip()
            for call in response.tool_calls
            if call.name == "request_help"
        )
        self._run_tool_calls(response)

        already = state.get("helped_once", False)
        pushback = self._verify() if not already else None
        if pushback is None or state.get("turn", 0) >= self._config.max_turns:
            return {
                "outcome": "help_requested",
                "detail": f"模型请求人工介入：{reason or '(未说明原因)'}",
            }

        return {
            "helped_once": True,
            "messages": [
                ModelMessage(
                    role="assistant", content=response.text or f"（请求协助：{reason}）"
                ),
                ModelMessage(role="user", content=pushback),
            ],
        }

    async def _invoke_with_retry(
        self, session: ModelSession, req: ModelRequest
    ) -> ModelResponse:
        """瞬时 5xx 重试。**只重试瞬时错误，且次数有上限。**

        ★ 2026-08-12 实测：百炼在多轮工具会话里约 40% 的运行会返回一次
          `500 internal_error`，网关自己的重试也扛不住。它是服务端瞬时故障
          （同样的请求下一次就成功），不是我们的载荷问题。

        ★ 但**不能无限重试**：真的坏了要让它坏出来。重试三次仍失败就如实上报
          model_error —— 把服务端故障伪装成「模型没做完」会把排查带到错误的方向。

        ★ 只重试 5xx / internal_error。4xx 是我们自己的问题，重试没有意义
          （而且会把一个确定性错误变成一个看起来随机的错误）。
        """

        last: Exception | None = None
        for attempt in range(1, _MODEL_RETRIES + 1):
            try:
                return await session.invoke(req)
            except Exception as exc:  # noqa: BLE001
                if not _is_transient(exc) or attempt == _MODEL_RETRIES:
                    raise
                last = exc
                logger.warning("模型瞬时错误，第 %d/%d 次重试：%s", attempt, _MODEL_RETRIES, exc)
                await asyncio.sleep(2.0 * attempt)
        raise last  # type: ignore[misc]  # 上面的循环必然 return 或 raise

    def _definition_of_done(self) -> str:
        """把「完成」的定义明确告诉模型。

        ★ prompt bundle 里已经带了 acceptance 谓词（B 的 packet-intent 会渲染它），
          但那是**描述性**的 —— 模型看到「kind: test, predicate: pytest …」
          并不知道这条会被真的执行。

          2026-08-12 实测：模型写完实现、跑 pytest 发现没有用例，
          于是**求助**而不是自己补上测试文件 —— 它把「没有测试」当成了
          环境问题，而不是自己没做完。

        ★ 所以这里说三件它必须知道的事：谓词会被执行 · 只写正文不算交付 ·
          缺什么就自己补，别把「我还差点东西」当成求助的理由。
          这不是哄模型，是把隐含契约写明。
        """

        return (
            "\n\n---\n"
            "## 完成的定义（这段由控制平面注入，不是建议）\n\n"
            "1. **验收谓词会被真的执行一遍**：`"
            + self._acceptance_predicate
            + "`。它不通过，这个任务就不算完成。\n"
            "2. **把代码写在回复正文里不算交付。** 只有 write_file 写进工作区的文件才算。\n"
            "3. 缺什么就自己补 —— 例如验收要跑测试而工作区里没有测试文件，"
            "那就是你还没写完，不是环境有问题。\n"
            "4. 规格已经完整，**不需要再向任何人确认** —— 直接把它做完。\n\n"
            "### 路径（这里最容易出错，看清楚）\n\n"
            "- 你的**当前工作目录就是工作区根**。`write_file` / `read_file` 的 path "
            "都相对于它。\n"
            "- 验收谓词也在这个根目录下执行。所以需求说「放在 workspace/ 下」时，"
            "文件的真实路径是 `workspace/xxx.py`。\n"
            "- **用 run_tests 时路径同样要带 workspace/**，例如 "
            '`["python","-m","pytest","workspace","-q"]`。\n'
            "- 找不到文件时先用 `list_files` 看一眼，**不要猜**。\n\n"
            "### 收尾\n\n"
            "验收谓词一过就**直接回复完成、不要再调工具**。\n"
            "反复跑 `git diff`、重复确认之类的动作只会耗光轮数 —— "
            "轮数用尽会被判失败，哪怕东西已经做对了。\n"
        )

    # ── 工具调用 ────────────────────────────────────────────

    def _run_tool_calls(self, response: ModelResponse) -> str:
        """执行本轮全部工具调用，把结果拼成一条 user 消息回传。"""

        chunks: list[str] = []
        for call in response.tool_calls:
            result = self._tools.execute(call.name, dict(call.input))
            self._append_transcript(
                {
                    "tool": call.name,
                    "input": dict(call.input),
                    "ok": result.ok,
                    "content": result.content[:2000],
                }
            )
            status = "成功" if result.ok else "失败"
            chunks.append(f"[工具 {call.name} {status}]\n{result.content}")
        return "\n\n".join(chunks)

    def _verify(self) -> str | None:
        """跑一遍验收谓词。通过返回 None；不通过返回**要喂回给模型的话**。

        ★ 为什么把真实输出原样喂回去：模型看到 "no tests ran" 才知道
          自己漏了测试文件；只说「验收未通过」它会去猜，而猜错的方向
          （改实现、换命令）比不动更糟。
        """

        command = split_command(self._acceptance_predicate)
        if not command:
            return None
        try:
            proc = subprocess.run(  # noqa: S603 - argv 明确，shell=False
                command,
                cwd=self._workspace,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120.0,
                stdin=subprocess.DEVNULL,
                shell=False,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            # ★ 跑不了就别拦着 —— 门禁那边还会再跑一次并如实报告。
            #   在这里把「我跑不动」误判成「你没做完」会把模型带偏。
            return None

        if proc.returncode == 0:
            # ★ 谓词过了还不够 —— **自验必须用和门禁一样的判据**。
            #
            #   2026-08-12 实测：模型被推回去补测试后，写了一句 `assert True`，
            #   谓词退出码 0，自验放行 —— 而门禁那边的空测试检查会拒绝它。
            #   模型在对着一个**比门禁更弱的标准**优化，于是永远差一层。
            #
            #   判据不一致比判据宽松更糟：它让模型以为自己做完了。
            vacuous = vacuity_check(self._workspace, command)
            if vacuous is None:
                return None
            return (
                f"[自动验收检查] 你还不能收尾。\n\n{vacuous}\n\n"
                "请把测试改成真正调用被测代码、并断言它的返回值 —— "
                "例如 `from subscriptions import monthly_total` 之后 "
                "`assert monthly_total([]) == 0.0`。"
            )

        tail = (proc.stdout + proc.stderr).strip()[-1500:]
        return (
            f"[自动验收检查] 你还不能收尾：验收谓词 `{self._acceptance_predicate}` "
            f"退出码 {proc.returncode}。\n\n{tail}\n\n"
            "请根据上面的真实输出把缺的东西补齐（例如验收要跑测试而工作区里"
            "还没有测试文件，那就用 write_file 把测试写出来），然后再收尾。"
        )

    # ── 收尾 ────────────────────────────────────────────────

    def _finish(
        self,
        prompt_digest: str,
        response: ModelResponse | None,
        turns: int,
        *,
        exhausted: bool = False,
    ) -> WorkerOutcome:
        written = tuple(self._tools.written_paths)
        self._write_transcript()

        if exhausted:
            # ★ 撞上限**不算完成**，哪怕中途写过文件 —— 模型没说"我做完了"，
            #   我们不能替它说。
            return self._fail(
                FailureCode.RUNTIME_ERROR,
                f"工具循环达到轮数上限 {self._config.max_turns}，模型仍未收尾",
                status="max_turns_exhausted",
                touched=written,
            )

        evidence = self._write_result(
            {
                "status": "completed" if written else "no_files_written",
                "model": self._req.routing.model,
                "role": self._req.role,
                "turns": turns,
                "written_paths": list(written),
                "prompt_digest": prompt_digest,
                "spent_cny": self._spent,
                "transcript_path": "tool_transcript.json",
                "response_path": "response.txt",
            }
        )
        if response is not None:
            (self._model_dir / "response.txt").write_text(response.text or "", encoding="utf-8")

        if not written:
            # ★ 模型说完事了但一个文件都没写 —— 如实报失败。
            #   控制平面那边还有 touched_paths 判据兜底，但**这里就该说清楚**：
            #   越早说，排查越容易。
            return WorkerFailed(
                reason_code=FailureCode.ACCEPTANCE_NOT_MET,
                detail="模型结束了会话，但一个文件都没有写入 —— 「写了字」不等于「交了活」",
                evidence=(evidence,),
                spent_cny=self._spent,
            )

        return WorkerCompleted(
            evidence=(evidence,),
            spent_cny=self._spent,
            touched_paths=written,
        )

    def _record_turn(self, turn: int, response: ModelResponse) -> None:
        self._append_transcript(
            {
                "turn": turn,
                "stop_reason": response.stop_reason,
                "text": (response.text or "")[:2000],
                "tool_calls": [
                    {"name": c.name, "input": dict(c.input)} for c in response.tool_calls
                ],
            }
        )

    def _append_transcript(self, entry: dict[str, Any]) -> None:
        """追加一条轨迹并**立刻落盘**。

        ★ 原来只在收尾时写一次，于是跑的过程中根本看不到模型在干什么 ——
          一次运行要 2–5 分钟，出问题时只能等它结束才知道原因。
          这跟 2026-08-11 那个 `_drain_stderr` 同类：
          **要能在出问题的当下取证，而不是事后。**

        ★ 每条都重写整个文件，效率不高但轨迹很短（十几条），
          换来的是任何时刻 `cat` 一下就能看到进度。
        """

        self._transcript.append(entry)
        self._write_transcript()

    def _write_transcript(self) -> None:
        self._model_dir.mkdir(parents=True, exist_ok=True)
        (self._model_dir / "tool_transcript.json").write_text(
            json.dumps(self._transcript, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def _harvest(self) -> dict[str, Any]:
        """把本次执行撞到的失败沉淀成 L0，够格的晋级为 L1。

        ★ 挂在 `_write_result` 上，因为它是**唯一的收尾漏斗** ——
          完成、失败、超时、撞轮次上限，全都经过这里。
          挂在「成功」分支上会漏掉最该学的那些次。

        ★ 进化层失败绝不能拖垮执行：一个学习机制把它正在学的东西搞崩了，
          是笔糟糕的交易。但**也不能静默吞掉** —— 那就成了
          skills manifest 里明令禁止的 silentDegrade。
          所以：吞异常、记日志、并把状态写进 result.json。
        """

        if self._config.memory_dir is None:
            return {"enabled": False, "reason": "memory_dir 未配置"}

        try:
            from codentum_harness.evolution import extract_observations
            from codentum_harness.evolution.promoter import (
                FingerprintLedger,
                record_and_promote,
            )
            from codentum_harness.memory_index import PersistentMemoryIndex

            observations = extract_observations(
                self._transcript, packet_id=str(self._req.packet_id)
            )
            if not observations:
                return {"enabled": True, "observations": 0, "promotions": 0}

            promotions = record_and_promote(
                PersistentMemoryIndex(self._config.memory_dir / "index"),
                FingerprintLedger(self._config.memory_dir / "fingerprints.json"),
                observations,
                packet_id=str(self._req.packet_id),
                role=self._req.role,
                created_at=_now_iso(),
            )
            return {
                "enabled": True,
                "observations": len(observations),
                "promotions": len(promotions),
                "promoted_refs": [p.ref for p in promotions],
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("进化层沉淀失败（不影响本次执行）：%s", exc)
            return {"enabled": True, "error": str(exc)}

    def _write_result(self, payload: dict[str, Any]) -> EvidenceRef:
        payload = {**payload, "evolution": self._harvest()}
        self._model_dir.mkdir(parents=True, exist_ok=True)
        (self._model_dir / "result.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        # ★ .as_posix()：EvidenceRef 是契约层字符串，两个平台上必须长得一样。
        relative = (self._model_dir / "result.json").relative_to(self._evidence_root).as_posix()
        return EvidenceRef(f"file:{relative}")

    def _fail(
        self,
        code: FailureCode,
        detail: str,
        *,
        status: str,
        touched: tuple[str, ...] = (),
    ) -> WorkerFailed:
        self._write_transcript()
        evidence = self._write_result(
            {
                "status": status,
                "detail": detail,
                "written_paths": list(touched or self._tools.written_paths),
                "spent_cny": self._spent,
                "transcript_path": "tool_transcript.json",
            }
        )
        return WorkerFailed(
            reason_code=code, detail=detail, evidence=(evidence,), spent_cny=self._spent
        )


_MODEL_RETRIES = 3

_TRANSIENT_MARKERS = ("internal_error", "500", "502", "503", "504", "timeout")


def _is_transient(exc: Exception) -> bool:
    """★ 只认瞬时错误。4xx 是我们自己的问题，重试只会把确定性错误
    变成看起来随机的错误。"""

    text = str(exc).lower()
    return any(marker in text for marker in _TRANSIENT_MARKERS)


def _session_spent(session: ModelSession) -> float:
    try:
        return float(session.spent_cny())
    except Exception:  # noqa: BLE001
        return 0.0
