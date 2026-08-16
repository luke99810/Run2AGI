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
from codentum_harness.model_gateway import MalformedToolArgumentsError
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


def test_self_verification_uses_the_same_criterion_as_the_gate(prepared: Path) -> None:
    """★ 自验必须用**和门禁一样的判据**，否则模型在对着更弱的标准优化。

    2026-08-12 实测：模型被推回去补测试后写了一句 `assert True`，
    谓词退出码 0、自验放行 —— 而门禁的空测试检查会拒绝它。
    模型于是永远差一层：它以为做完了，门禁说没有。

    **判据不一致比判据宽松更糟：它让模型以为自己做完了。**
    """

    gateway = _FakeGateway(
        [
            # 先写实现 + 一个空测试
            ModelResponse(
                text="",
                tool_calls=(
                    ToolCall(id="1", name="write_file", input={"path": "impl.py", "content": "def f():\n    return 1\n"}),
                    ToolCall(id="2", name="write_file", input={"path": "test_impl.py", "content": "def test_x():\n    assert True\n"}),
                ),
                stop_reason="tool_use",
                usage=_usage(),
            ),
            ModelResponse(text="做完了", tool_calls=(), stop_reason="end", usage=_usage()),
            # 被推回去之后写真测试
            ModelResponse(
                text="",
                tool_calls=(
                    ToolCall(
                        id="3",
                        name="write_file",
                        input={"path": "test_impl.py", "content": "from impl import f\n\n\ndef test_x():\n    assert f() == 1\n"},
                    ),
                ),
                stop_reason="tool_use",
                usage=_usage(),
            ),
            ModelResponse(text="这次真做完了", tool_calls=(), stop_reason="end", usage=_usage()),
        ]
    )
    runner = build_agent_runner(
        AgentRunnerConfig(
            gateway=gateway,  # type: ignore[arg-type]
            acceptance_predicate=f'"{sys.executable}" -m pytest . -q',
        )
    )
    outcome = runner(_spawn(prepared))

    assert outcome.status == "completed", getattr(outcome, "detail", "")
    # ★ 关键：空测试那一轮**没有**被放行
    assert len(gateway.session.seen) == 4, f"只跑了 {len(gateway.session.seen)} 轮"
    joined = "\n".join(m.content for m in gateway.session.seen[2].messages)
    assert "验收测试是空的" in joined


def test_truncated_tool_arguments_are_recoverable_not_fatal(prepared: Path) -> None:
    """★ 工具调用参数被截断（输出超长）时，告诉模型重写，而不是直接判失败。

    2026-08-12 实测：模型写较大的测试文件时，arguments 在
    `result = add_subscription(` 处断掉 —— JSON 真的不完整，救不回来。
    但模型自己能修，只要告诉它「上次被截断了，写小一点」。

    **把一个可恢复的情况当成终局，是在浪费一次本来能成的运行。**
    """

    class _TruncatingSession(_FakeSession):
        async def invoke(self, req: ModelRequest) -> ModelResponse:
            self.seen.append(req)
            if len(self.seen) == 1:
                # ★ 用生产代码真正抛的那个类型，而不是裸 ValueError。
                #   夹具与生产不一致时，这条测试守的就不是真的那条路径了 ——
                #   2026-08-16 改成按类型分支后，它正是以这种方式失败的。
                raise MalformedToolArgumentsError(
                    "response field 'arguments' must be JSON object text; got '{\"content\": \"…"
                )
            return ModelResponse(
                text="",
                tool_calls=(ToolCall(id="1", name="write_file", input={"path": "a.py", "content": "x=1"}),)
                if len(self.seen) == 2
                else (),
                stop_reason="tool_use" if len(self.seen) == 2 else "end",
                usage=_usage(),
            )

    gateway = _FakeGateway([])
    gateway.session = _TruncatingSession(script=[], seen=[])
    runner = build_agent_runner(AgentRunnerConfig(gateway=gateway))  # type: ignore[arg-type]
    outcome = runner(_spawn(prepared))

    assert outcome.status == "completed", getattr(outcome, "detail", "")
    joined = "\n".join(m.content for m in gateway.session.seen[1].messages)
    # ★ 守「模型拿到了可操作的建议」，不钉死某一句文案 ——
    #   钉死文案会让每次措辞调整都变成一次假失败，
    #   而真正要保住的是「它知道该怎么改」。
    assert "写短一些" in joined, "没告诉模型该怎么改，那就不是自我纠正"
    assert "JSON object text" in joined, "没把实际原因带给模型"


# ══════════════════════════════════════════════════════════════
#  进化层接线
#
#  ★ 这两条守的不是「进化层算得对不对」（那在 harness 那两组测），
#    而是**它到底有没有被接上**。
#
#    这个仓库已经栽过两次同样的跟头：langgraph 装在依赖里零 import；
#    mcp_config_dir 定义了但全仓库无人传。两次都是「结构完整、从未被调用」，
#    而且两次都**没有任何测试会红** —— 因为大家测的都是模块本身。
#    模块本身当然是对的，它只是没接上。
# ══════════════════════════════════════════════════════════════


def _failing_tool_script() -> list[ModelResponse]:
    """让模型往工作区外面写 —— ToolExecutor 会拒绝，于是产生一条真实失败。"""

    return [
        ModelResponse(
            text="",
            tool_calls=(ToolCall(id="1", name="write_file", input={"path": "../outside.txt", "content": "x"}),),
            stop_reason="tool_use",
            usage=_usage(),
        ),
        ModelResponse(text="放弃", tool_calls=(), stop_reason="end", usage=_usage()),
    ]


def _result_json(workspace: Path) -> dict[str, Any]:
    path = workspace / ".codentum" / "evidence" / "wp-toolloop01-attempt-1" / "model" / "result.json"
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def test_evolution_off_is_visible_not_silent(prepared: Path) -> None:
    """★ 没配 memory_dir = 不沉淀经验，而这件事必须**看得见**。

    静默关闭的后果：「忘了接线」和「还没攒够晋级次数」在外面完全不可区分，
    而后者是正常状态 —— 于是前者可以躺很久没人发现。
    """

    _run(prepared, _failing_tool_script())
    assert _result_json(prepared)["evolution"] == {"enabled": False, "reason": "memory_dir 未配置"}


def test_failed_tool_call_becomes_a_persisted_observation(prepared: Path, tmp_path: Path) -> None:
    """★ 端到端：一次真实的工具失败 → 磁盘上一条 L0。

    断言落在**磁盘**而不是返回值上 —— 进化层的意义就是跨执行留下东西，
    只在内存里对过一次的话，下一个 packet 什么也读不到。
    """

    memory = tmp_path / "mem"
    _run(prepared, _failing_tool_script(), memory_dir=memory)

    evolution = _result_json(prepared)["evolution"]
    assert evolution["enabled"] is True
    assert evolution["observations"] >= 1, f"工具失败没被沉淀：{evolution}"
    assert evolution["promotions"] == 0, "只撞过一个 packet，不该晋级"

    written = list((memory / "index").rglob("*.json"))
    assert written, "result.json 说沉淀了，磁盘上却没有 —— 这正是要防的那种「报告成功」"


def test_mcp_tools_reach_the_model_tool_surface(prepared: Path, tmp_path: Path) -> None:
    """★ 「第三方应用接进主 Agent」这句话的**实际含义**就是这一条：
    MCP 的工具真的出现在模型看到的工具面上，和内置工具并排。

    前面那几条守的是「连上了」「只连一次」「报告落盘」——
    但全都连上了、模型却看不到，从结果上和没接是一样的。
    这条是链路的最后一段：config → engine → runner → **模型请求**。

    ★ 用真子进程 server（复用 test_mcp_client 的最小实现），不是打桩。
      打桩能证明代码路径通，证明不了协议握手真的成立。
    """

    import sys

    from codentum_engine.mcp_client import McpServerConfig
    from codentum_engine.mcp_toolbox import McpToolbox
    from test_mcp_client import _FAKE_SERVER

    script = tmp_path / "fake_mcp_server.py"
    script.write_text(_FAKE_SERVER, encoding="utf-8")

    toolbox = McpToolbox()
    toolbox.connect_all((
        McpServerConfig(
            id="github", name="GitHub", command=sys.executable, args=(str(script),)
        ),
    ))
    try:
        _, gateway = _run(
            prepared,
            [ModelResponse(text="不用工具", tool_calls=(), stop_reason="end", usage=_usage())],
            mcp_toolbox=toolbox,
        )
        offered = {t.name for t in gateway.session.seen[0].tools}
        assert "write_file" in offered, "内置工具不该因为接了 MCP 而消失"
        assert "github__create_issue" in offered, f"MCP 工具没进工具面：{sorted(offered)}"
    finally:
        toolbox.close()


# ══════════════════════════════════════════════════════════════
#  OTel 导出 —— 守的是**接线**，不是编码器
# ══════════════════════════════════════════════════════════════
#
# ★ 这一组是本项目那类缺陷的又一个样本：`otel.py` 从落地起就
#   **只有它自己的测试在 import 它**，生产路径零调用 —— 真跑一次
#   产不出任何 span，而材料里写的是「OTel 可观测已落地」，
#   验证命令给的是 `pytest test_otel.py`。那条命令是绿的，
#   它证明的是「编码器会编码」，不是「系统会产 trace」。
#
#   所以这组测试**不测编码器**（那是 test_otel.py 的事），
#   只测一件事：**跑完一次，evidence 里有没有 trace。**
#   把接线拆掉，这组必须红。


def _trace(workspace: Path) -> dict[str, Any]:
    path = workspace / ".codentum" / "evidence" / "wp-toolloop01-attempt-1" / "model" / "trace.otlp.json"
    assert path.exists(), "跑完一次却没有产出 trace —— 导出没接上生产路径"
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return payload


def _spans(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        span
        for resource in payload["resourceSpans"]
        for scope in resource["scopeSpans"]
        for span in scope["spans"]
    ]


def test_a_real_run_emits_an_otlp_trace(prepared: Path) -> None:
    """★ 「已落地」必须意味着**真跑一次就有 trace**。

    在这条接上之前，otel.py 的全部测试都是绿的，而系统产出零条 span。
    """

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

    spans = _spans(_trace(prepared))
    names = [s["name"] for s in spans]
    assert any(n.startswith("chat ") for n in names), f"没有 chat span：{names}"
    assert any(n.startswith("execute_tool ") for n in names), f"工具调用没进 trace：{names}"


def test_every_turn_shares_one_trace_id(prepared: Path) -> None:
    """★ 一次 packet 执行是**一条** trace，多轮是它下面的多条 span。

    trace_id 若逐轮派生，导出的就是 N 条互不相干的 trace ——
    文件照样存在、测试照样绿，但那不是一条能看的调用链。
    而每轮同 seed 又会让多个 chat span 撞同一个 span_id。
    这条同时守住这两侧。
    """

    _run(
        prepared,
        [
            ModelResponse(
                text="",
                tool_calls=(ToolCall(id="1", name="write_file", input={"path": "a.py", "content": "x"}),),
                stop_reason="tool_use",
                usage=_usage(),
            ),
            ModelResponse(
                text="",
                tool_calls=(ToolCall(id="2", name="write_file", input={"path": "b.py", "content": "y"}),),
                stop_reason="tool_use",
                usage=_usage(),
            ),
            ModelResponse(text="ok", tool_calls=(), stop_reason="end", usage=_usage()),
        ],
    )

    spans = _spans(_trace(prepared))
    assert len({s["traceId"] for s in spans}) == 1, "多轮被拆成了多条 trace"
    chat_ids = [s["spanId"] for s in spans if s["name"].startswith("chat ")]
    assert len(chat_ids) == len(set(chat_ids)) >= 3, f"每轮的 chat span 要各有各的 id：{chat_ids}"


def test_span_carries_the_real_model_role_and_cost(prepared: Path) -> None:
    """★ trace 的价值在于**内容是真的**。

    模型名、角色、成本若是占位值，这条 trace 看起来完全正常
    而说的全不对 —— 那比没有 trace 更坏。
    """

    _run(prepared, [ModelResponse(text="ok", tool_calls=(), stop_reason="end", usage=_usage())])

    chat = next(s for s in _spans(_trace(prepared)) if s["name"].startswith("chat "))
    attrs = {a["key"]: next(iter(a["value"].values())) for a in chat["attributes"]}
    assert attrs["gen_ai.request.model"] == "fake-model", "模型名不是这次真的用的那个"
    assert attrs["codentum.role"] == "coder"
    assert attrs["gen_ai.usage.input_tokens"] == 10
    assert attrs["gen_ai.system"] == "dashscope"


def test_span_timestamps_are_real_wall_clock(prepared: Path) -> None:
    """★ 时间戳是 0 的 span 在任何 Collector 里都排不出先后。

    这是最容易「接上了但没接全」的一处：span 产出来了、结构也对，
    只是时间字段全是默认值 —— 而那样的 trace 画不出时序图。
    """

    _run(prepared, [ModelResponse(text="ok", tool_calls=(), stop_reason="end", usage=_usage())])

    chat = next(s for s in _spans(_trace(prepared)) if s["name"].startswith("chat "))
    start = int(chat["startTimeUnixNano"])
    end = int(chat["endTimeUnixNano"])
    assert start > 1_700_000_000_000_000_000, "开始时间不是真实挂钟"
    assert end >= start, "结束早于开始"


def test_export_failure_does_not_kill_the_run(prepared: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """★ 可观测是旁路 —— 它把主链路搞崩是笔糟糕的交易。

    但也不能静默：失败会记日志（同 `_harvest` 的取舍）。
    """

    import codentum_engine.agent_runner as runner_mod

    def _boom(**_kw: Any) -> Any:
        raise RuntimeError("导出炸了")

    monkeypatch.setattr(runner_mod, "genai_spans_from_model_response", _boom)

    outcome, _ = _run(
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

    assert outcome.status == "completed", "导出失败拖垮了主链路"
    assert (prepared / "a.py").exists()
