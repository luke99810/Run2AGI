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


def build_agent_runner(config: AgentRunnerConfig):  # type: ignore[no-untyped-def]
    """造一个 `WorkerRunner`（`Callable[[SpawnRequest], WorkerOutcome]`）。"""

    def run(req: SpawnRequest) -> WorkerOutcome:
        return _AgentRun(config, req).execute()

    return run


class _AgentRun:
    def __init__(self, config: AgentRunnerConfig, req: SpawnRequest) -> None:
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
        messages: list[ModelMessage] = [ModelMessage(role="user", content=prompt.user)]

        try:
            session = await self._config.gateway.open(
                self._req.role, self._req.routing, self._req.budget.limit_cny
            )
            for turn in range(1, self._config.max_turns + 1):
                response = await session.invoke(
                    ModelRequest(
                        system=prompt.system,
                        messages=tuple(messages),
                        effort=self._req.routing.effort,
                        tools=tools,
                    )
                )
                self._spent = max(self._spent, _session_spent(session))
                self._record_turn(turn, response)

                if not response.tool_calls:
                    # 模型不再要求调工具 —— 循环结束，成败由是否落了文件判定
                    return self._finish(prompt.digest, response, turn)

                if any(call.name == "request_help" for call in response.tool_calls):
                    # ★ 求助必须**终止**循环，不能当成普通工具调用。
                    #
                    #   第一版把 request_help 也返回 ok=True，等于告诉模型
                    #   「求助成功了，你继续」—— 于是它每一轮都重复求助，
                    #   12 轮全部烧完、一个文件没写。2026-08-12 实测到的正是这个。
                    #
                    #   语义上也该如此：求助的意思是「我需要人来决定」，
                    #   那就不该再让模型自己往下猜。
                    self._run_tool_calls(response)
                    reason = next(
                        str(call.input.get("reason", "")).strip()
                        for call in response.tool_calls
                        if call.name == "request_help"
                    )
                    return self._fail(
                        FailureCode.ACCEPTANCE_NOT_MET,
                        f"模型请求人工介入：{reason or '(未说明原因)'}",
                        status="help_requested",
                    )

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


def _session_spent(session: ModelSession) -> float:
    try:
        return float(session.spent_cny())
    except Exception:  # noqa: BLE001
        return 0.0
