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

from .acceptance import _split_command
from .tools import ToolExecutor, tool_schemas_for

__all__ = ["AgentRunnerConfig", "build_agent_runner"]

logger = logging.getLogger(__name__)

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
        self._tools = ToolExecutor(self._workspace)
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
            for turn in range(1, self._config.max_turns + 1):
                response = await self._invoke_with_retry(
                    session,
                    ModelRequest(
                        system=prompt.system,
                        messages=tuple(messages),
                        effort=self._req.routing.effort,
                        tools=tools,
                    ),
                )
                self._spent = max(self._spent, _session_spent(session))
                self._record_turn(turn, response)

                if not response.tool_calls:
                    # ★ 模型说完事了 —— 但**先把验收谓词跑一遍再放它走**。
                    #
                    #   验收谓词就是「完成」的定义。让模型在谓词不过时收尾，
                    #   等于让它自己宣布达标 —— 那正是本项目一路在拆的那个病。
                    #
                    #   2026-08-12 实测：模型写完实现就停手/求助，测试文件从来没写过。
                    #   把真实的失败输出喂回去之后，它才知道自己还没做完。
                    verdict = self._verify()
                    if verdict is None or turn >= self._config.max_turns:
                        return self._finish(prompt.digest, response, turn)

                    messages.append(
                        ModelMessage(role="assistant", content=response.text or "（我认为已完成）")
                    )
                    messages.append(ModelMessage(role="user", content=verdict))
                    continue

                if any(call.name == "request_help" for call in response.tool_calls):
                    reason = next(
                        str(call.input.get("reason", "")).strip()
                        for call in response.tool_calls
                        if call.name == "request_help"
                    )
                    self._run_tool_calls(response)
                    pushback = self._verify() if not self._helped_once else None

                    if pushback is None or turn >= self._config.max_turns:
                        # ★ 求助**终止**循环：它的语义是「我需要人来决定」，
                        #   不该再让模型自己往下猜。
                        #   （第一版把 request_help 也返回 ok=True，等于告诉它
                        #   「求助成功了，你继续」，于是它每轮重复求助、
                        #   12 轮烧完、一个文件没写。）
                        return self._fail(
                            FailureCode.ACCEPTANCE_NOT_MET,
                            f"模型请求人工介入：{reason or '(未说明原因)'}",
                            status="help_requested",
                        )

                    # ★ 但**第一次**求助先给一次事实性回推。
                    #
                    #   2026-08-12 实测：模型写完实现就求助「需要具体的验收条件」——
                    #   而验收条件一直在 prompt 里，它只是没把「谓词跑不过」
                    #   当成自己的事。把谓词的真实输出摆给它看，
                    #   比重复一遍要求有效得多。
                    #
                    #   只回推一次：再求助就是真的卡住了，那就交给人。
                    self._helped_once = True
                    messages.append(
                        ModelMessage(role="assistant", content=response.text or f"（请求协助：{reason}）")
                    )
                    messages.append(ModelMessage(role="user", content=pushback))
                    continue

                # ★ 不能发 content="" 的 assistant 消息。
                #   模型只调工具、不说话时 response.text 是空的，而空 content
                #   会被百炼判成畸形请求（2026-08-12 实测连续两次 500
                #   internal_error，都发生在多轮的第 2–3 轮）。
                #   补一句它自己做了什么，既合法，也让下一轮有上下文。
                messages.append(
                    ModelMessage(
                        role="assistant",
                        content=response.text
                        or "（调用工具：" + "、".join(c.name for c in response.tool_calls) + "）",
                    )
                )
                messages.append(
                    ModelMessage(role="user", content=self._run_tool_calls(response))
                )
            return self._finish(prompt.digest, None, self._config.max_turns, exhausted=True)
        except Exception as exc:  # noqa: BLE001
            return self._fail(FailureCode.MODEL_ERROR, str(exc), status="failed")
        finally:
            if session is not None:
                await session.close()

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
            "4. 规格已经完整，**不需要再向任何人确认** —— 直接把它做完。\n"
        )

    # ── 工具调用 ────────────────────────────────────────────

    def _run_tool_calls(self, response: ModelResponse) -> str:
        """执行本轮全部工具调用，把结果拼成一条 user 消息回传。"""

        chunks: list[str] = []
        for call in response.tool_calls:
            result = self._tools.execute(call.name, dict(call.input))
            self._transcript.append(
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

        command = _split_command(self._acceptance_predicate)
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
            return None

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
        self._transcript.append(
            {
                "turn": turn,
                "stop_reason": response.stop_reason,
                "text": (response.text or "")[:2000],
                "tool_calls": [
                    {"name": c.name, "input": dict(c.input)} for c in response.tool_calls
                ],
            }
        )

    def _write_transcript(self) -> None:
        self._model_dir.mkdir(parents=True, exist_ok=True)
        (self._model_dir / "tool_transcript.json").write_text(
            json.dumps(self._transcript, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def _write_result(self, payload: dict[str, Any]) -> EvidenceRef:
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
