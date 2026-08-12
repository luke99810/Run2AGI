"""工具循环的判据 —— 用假网关，不烧钱也不依赖网络。

★ 真实模型那一条在 `tests/e2e/`，需要 Key。这里守的是**循环本身的行为**：
  工具结果有没有回传、撞上限会不会伪装成完成、没写文件会不会误报成功。
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from codentum_contracts.interfaces import (
    BudgetGrantRuntime,
    ModelRequest,
    ModelResponse,
    SpawnRequest,
    ToolCall,
    Usage,
)
from codentum_contracts.state import ModelRouting, PacketId
from codentum_engine.agent_runner import AgentRunnerConfig, build_agent_runner
from codentum_harness.prompt_bundle import write_worker_prompt_bundle
from codentum_roles.loader import load_builtin_role_specs


# ── 假网关 ──────────────────────────────────────────────────


@dataclass
class _FakeSession:
    """按脚本逐轮返回预设响应，并记录收到的请求。"""

    script: list[ModelResponse]
    seen: list[ModelRequest]

    session_id: str = "fake-session"
    model: str = "fake-model"
    role: str = "coder"

    async def invoke(self, req: ModelRequest) -> ModelResponse:
        self.seen.append(req)
        if not self.script:
            return ModelResponse(text="done", tool_calls=(), stop_reason="end", usage=_usage())
        return self.script.pop(0)

    def stream(self, req: ModelRequest) -> Any:  # pragma: no cover - 未使用
        raise NotImplementedError

    def spent_cny(self) -> float:
        return 0.25

    async def close(self) -> None:
        return None


class _FakeGateway:
    def __init__(self, script: list[ModelResponse]) -> None:
        self.session = _FakeSession(script=script, seen=[])

    async def open(self, role: str, routing: ModelRouting, grant_cny: float) -> _FakeSession:
        return self.session

    async def estimate(self, routing: ModelRouting, req: ModelRequest) -> Any:  # pragma: no cover
        raise NotImplementedError

    async def ledger(self) -> Any:  # pragma: no cover
        raise NotImplementedError


def _usage() -> Usage:
    return Usage(input_tokens=10, output_tokens=10, cached_input_tokens=0, cost_cny=0.0)


def _spawn(workspace: Path, tools: tuple[str, ...] = ("write_file", "read_file")) -> SpawnRequest:
    return SpawnRequest(
        packet_id=PacketId("wp-toolloop01"),
        role="coder",
        mounts=(),
        tools=tools,
        routing=ModelRouting(model="fake-model", effort="medium"),
        budget=BudgetGrantRuntime(limit_cny=1.0, degradation_chain=()),
        workspace=str(workspace),
        attempt=1,
    )


@pytest.fixture
def prepared(tmp_path: Path) -> Path:
    """铺出 runner 需要的 prompt bundle。

    ★ 用 harness 真实的 `write_worker_prompt_bundle` 写，而不是手搓 manifest ——
      manifest 里的 digest 会被 `load_worker_prompt_bundle` 校验，
      手搓的形状一旦与真实实现漂移，这组测试会以「加载失败」的面目失败，
      而真正的原因是 fixture 假了。**测试的输入也该是真的。**
    """

    workspace = tmp_path / "ws"
    evidence = workspace / ".codentum" / "evidence" / "wp-toolloop01-attempt-1"
    evidence.mkdir(parents=True)
    write_worker_prompt_bundle(
        request=_spawn(workspace),
        role_spec=next(spec for spec in load_builtin_role_specs() if spec.id == "coder"),
        evidence_dir=evidence,
    )
    return workspace


def _run(workspace: Path, script: list[ModelResponse], **kw: Any):  # type: ignore[no-untyped-def]
    gateway = _FakeGateway(script)
    runner = build_agent_runner(AgentRunnerConfig(gateway=gateway, **kw))  # type: ignore[arg-type]
    return runner(_spawn(workspace)), gateway


# ══════════════════════════════════════════════════════════════


def test_model_can_actually_write_a_file(prepared: Path) -> None:
    """★ 这是整个改动要证明的那件事：模型调 write_file，文件真的落盘。

    在工具循环之前，模型只能把代码写在回复正文里，`tool_calls` 恒为空，
    工作区一个文件都不会被创建。
    """

    outcome, _ = _run(
        prepared,
        [
            ModelResponse(
                text="我来写文件",
                tool_calls=(
                    ToolCall(id="1", name="write_file", input={"path": "app.py", "content": "def add(a,b):\n    return a+b\n"}),
                ),
                stop_reason="tool_use",
                usage=_usage(),
            ),
            ModelResponse(text="写完了", tool_calls=(), stop_reason="end", usage=_usage()),
        ],
    )

    assert outcome.status == "completed", getattr(outcome, "detail", "")
    assert outcome.touched_paths == ("app.py",)
    assert (prepared / "app.py").read_text(encoding="utf-8").startswith("def add")


def test_tool_result_is_fed_back_to_the_model(prepared: Path) -> None:
    """★ 循环的核心：工具执行结果必须回到下一轮的消息里。

    不回传的话，模型看不到自己那次调用成没成功，只能瞎猜 ——
    那就退化成了「一次性发命令」，不是工具循环。
    """

    _, gateway = _run(
        prepared,
        [
            ModelResponse(
                text="",
                tool_calls=(ToolCall(id="1", name="write_file", input={"path": "a.py", "content": "x"}),),
                stop_reason="tool_use",
                usage=_usage(),
            ),
            ModelResponse(text="ok", tool_calls=(), stop_reason="end", usage=_usage()),
        ],
    )

    second = gateway.session.seen[1]
    joined = "\n".join(m.content for m in second.messages)
    assert "工具 write_file 成功" in joined
    assert "已写入 a.py" in joined


def test_tool_failure_is_reported_truthfully_not_swallowed(prepared: Path) -> None:
    """★ 工具失败要如实回传，让模型有机会自己纠正。

    吞掉失败后返回成功，模型会以为写成功了继续往下走 —— 比直接失败更糟。
    """

    _, gateway = _run(
        prepared,
        [
            ModelResponse(
                text="",
                tool_calls=(ToolCall(id="1", name="write_file", input={"path": "../escape.py", "content": "x"}),),
                stop_reason="tool_use",
                usage=_usage(),
            ),
            ModelResponse(text="换个路径", tool_calls=(), stop_reason="end", usage=_usage()),
        ],
    )

    joined = "\n".join(m.content for m in gateway.session.seen[1].messages)
    assert "工具 write_file 失败" in joined
    assert "越出工作区" in joined


def test_finishing_without_writing_any_file_is_a_failure(prepared: Path) -> None:
    """★ 模型说完事了但一个文件都没写 → 判失败，不是成功。

    「写了字」不等于「交了活」。控制平面那边还有 touched_paths 判据兜底，
    但这里就该说清楚 —— 越早说，排查越容易。
    """

    outcome, _ = _run(
        prepared,
        [ModelResponse(text="这是代码：def add()...", tool_calls=(), stop_reason="end", usage=_usage())],
    )

    assert outcome.status == "failed"
    assert outcome.reason_code.value == "acceptance_not_met"
    assert "一个文件都没有写入" in outcome.detail


def test_hitting_the_turn_limit_is_not_reported_as_completed(prepared: Path) -> None:
    """★ 撞轮数上限不算完成，**哪怕中途写过文件**。

    模型没说「我做完了」，我们不能替它说 —— 那正是本项目追了四层的那个病。
    """

    loop_forever = [
        ModelResponse(
            text="",
            tool_calls=(ToolCall(id="x", name="write_file", input={"path": "a.py", "content": "x"}),),
            stop_reason="tool_use",
            usage=_usage(),
        )
        for _ in range(10)
    ]
    outcome, _ = _run(prepared, loop_forever, max_turns=3)

    assert outcome.status == "failed"
    assert "轮数上限" in outcome.detail
    assert (prepared / "a.py").exists(), "前置条件：中途确实写过文件"


def test_role_without_implemented_tools_fails_loudly(prepared: Path) -> None:
    """★ 没有可用工具时**不要静默退化成 one-shot**。

    那会让「模型没写文件」看起来像模型的问题，实际是配置问题。
    """

    gateway = _FakeGateway([])
    runner = build_agent_runner(AgentRunnerConfig(gateway=gateway))  # type: ignore[arg-type]
    outcome = runner(_spawn(prepared, tools=("尚未实现的工具",)))

    assert outcome.status == "failed"
    assert "没有任何已实现的工具" in outcome.detail


def test_transcript_records_every_tool_call(prepared: Path) -> None:
    """★ 工具调用要留痕：出问题时得能回答「模型到底做了什么」。"""

    _run(
        prepared,
        [
            ModelResponse(
                text="",
                tool_calls=(ToolCall(id="1", name="write_file", input={"path": "a.py", "content": "x"}),),
                stop_reason="tool_use",
                usage=_usage(),
            ),
            ModelResponse(text="ok", tool_calls=(), stop_reason="end", usage=_usage()),
        ],
    )

    transcript = json.loads(
        (prepared / ".codentum" / "evidence" / "wp-toolloop01-attempt-1" / "model" / "tool_transcript.json")
        .read_text(encoding="utf-8")
    )
    assert any(entry.get("tool") == "write_file" and entry.get("ok") for entry in transcript)


def test_request_help_ends_the_loop_instead_of_burning_turns(prepared: Path) -> None:
    """★ 求助必须**终止**循环，不能当成普通工具调用。

    第一版把 request_help 也返回 ok=True，等于告诉模型「求助成功了，你继续」——
    2026-08-12 实测：模型每一轮都重复同一句求助，12 轮全烧完、一个文件没写。

    语义上也该如此：求助的意思是「我需要人来决定」，
    那就不该再让模型自己往下猜。
    """

    help_forever = [
        ModelResponse(
            text="",
            tool_calls=(ToolCall(id="h", name="request_help", input={"reason": "验收标准不明确"}),),
            stop_reason="tool_use",
            usage=_usage(),
        )
        for _ in range(10)
    ]
    outcome, gateway = _run(prepared, help_forever, max_turns=10)

    assert outcome.status == "failed"
    assert "请求人工介入" in outcome.detail
    assert "验收标准不明确" in outcome.detail
    # ★ 关键：**最多两轮**（第一次求助给一次事实性回推，再求助即终止），
    #   而不是把 10 轮全烧完。
    assert len(gateway.session.seen) <= 2, f"求助后仍继续循环了 {len(gateway.session.seen)} 轮"


def test_model_cannot_finish_while_the_acceptance_predicate_fails(prepared: Path) -> None:
    """★ 模型说「我做完了」不算数 —— 验收谓词不过就把它推回去继续。

    验收谓词就是「完成」的定义。让模型在谓词不过时收尾，
    等于让它自己宣布达标 —— 那正是本项目一路在拆的那个病。

    2026-08-12 实测：模型写完实现就停手（或求助），测试文件从来没写过。
    把**真实的失败输出**喂回去之后，它才知道自己还没做完。
    """

    gateway = _FakeGateway(
        [
            # 第 1 轮：写个文件就想收工
            ModelResponse(
                text="",
                tool_calls=(ToolCall(id="1", name="write_file", input={"path": "a.py", "content": "x=1"}),),
                stop_reason="tool_use",
                usage=_usage(),
            ),
            ModelResponse(text="我做完了", tool_calls=(), stop_reason="end", usage=_usage()),
            # 被推回去之后才补上第二个文件
            ModelResponse(
                text="",
                tool_calls=(ToolCall(id="2", name="write_file", input={"path": "b.py", "content": "y=2"}),),
                stop_reason="tool_use",
                usage=_usage(),
            ),
            ModelResponse(text="这次真做完了", tool_calls=(), stop_reason="end", usage=_usage()),
        ]
    )
    # 谓词：b.py 存在才算通过
    predicate = (
        f'"{sys.executable}" -c "import pathlib,sys; '
        "sys.exit(0 if pathlib.Path('b.py').exists() else 1)\""
    )
    runner = build_agent_runner(
        AgentRunnerConfig(gateway=gateway, acceptance_predicate=predicate)  # type: ignore[arg-type]
    )
    outcome = runner(_spawn(prepared))

    assert outcome.status == "completed", getattr(outcome, "detail", "")
    # ★ 关键：它被推回去了 —— 不是两轮就结束
    assert len(gateway.session.seen) == 4, f"只跑了 {len(gateway.session.seen)} 轮，没有被推回去"
    joined = "\n".join(m.content for m in gateway.session.seen[2].messages)
    assert "你还不能收尾" in joined


def test_first_help_request_gets_one_factual_pushback(prepared: Path) -> None:
    """★ 第一次求助先给一次**事实性**回推，再求助才终止。

    2026-08-12 实测：模型写完实现就求助「需要具体的验收条件」——
    而验收条件一直在 prompt 里，它只是没把「谓词跑不过」当成自己的事。
    把谓词的**真实输出**摆给它看，比重复一遍要求有效得多。

    只回推一次：再求助就是真的卡住了，那就交给人。
    """

    gateway = _FakeGateway(
        [
            ModelResponse(
                text="",
                tool_calls=(ToolCall(id="h", name="request_help", input={"reason": "不知道要测什么"}),),
                stop_reason="tool_use",
                usage=_usage(),
            ),
            # 回推之后自己补上文件
            ModelResponse(
                text="",
                tool_calls=(ToolCall(id="w", name="write_file", input={"path": "b.py", "content": "y=2"}),),
                stop_reason="tool_use",
                usage=_usage(),
            ),
            ModelResponse(text="做完了", tool_calls=(), stop_reason="end", usage=_usage()),
        ]
    )
    predicate = (
        f'"{sys.executable}" -c "import pathlib,sys; '
        "sys.exit(0 if pathlib.Path('b.py').exists() else 1)\""
    )
    runner = build_agent_runner(
        AgentRunnerConfig(gateway=gateway, acceptance_predicate=predicate)  # type: ignore[arg-type]
    )
    outcome = runner(_spawn(prepared))

    # ★ 回推奏效：本来会以 help_requested 失败，现在真的做完了
    assert outcome.status == "completed", getattr(outcome, "detail", "")
    joined = "\n".join(m.content for m in gateway.session.seen[1].messages)
    assert "你还不能收尾" in joined


def test_transient_model_errors_are_retried_but_permanent_ones_are_not(prepared: Path) -> None:
    """★ 瞬时 5xx 重试；4xx 立刻失败。

    2026-08-12 实测：百炼在多轮工具会话里约 40% 的运行返回一次
    `500 internal_error`，同样的请求下一次就成功。

    ★ 但 4xx 不能重试 —— 那是我们自己的问题，重试只会把一个确定性错误
      变成一个看起来随机的错误。
    """

    from codentum_engine.agent_runner import _is_transient

    assert _is_transient(RuntimeError("Error code: 500 - internal_error")) is True
    assert _is_transient(RuntimeError("Error code: 503")) is True
    assert _is_transient(RuntimeError("Error code: 400 - invalid_request")) is False
    assert _is_transient(RuntimeError("Error code: 401 - unauthorized")) is False
