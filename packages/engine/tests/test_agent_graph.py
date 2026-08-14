"""执行平面控制流图的判据（ADR-0004）。

★ 这组测试是 LangGraph 改造带来的**新能力**：路由是纯函数，
  可以脱离模型、文件系统、网络单独验证。

  在手写 `for turn in range(...)` 循环里，「下一步走哪」的判断与副作用
  （调模型、写文件、跑谓词）是混在一起的 —— 要测一个分支就得把整条链路
  都搭起来。图把两者分开之后，**边的逻辑可以毫秒级穷举**。

★ 与 `test_agent_runner.py` 的分工：
  那组测端到端语义（用假网关跑完整 runner），这组测图的形状与路由。
  两组都要有 —— 只有前者时，一条边写错要靠端到端才发现；
  只有后者时，节点与图的接线错误无人覆盖。
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END

from codentum_contracts.interfaces import ModelResponse, ToolCall, Usage
from codentum_engine.agent_graph import (
    AgentGraphState,
    build_agent_graph,
    route_after_help,
    route_after_model,
    route_after_verify,
)


def _usage() -> Usage:
    return Usage(input_tokens=1, output_tokens=1, cached_input_tokens=0, cost_cny=0.0)


def _response(*, tools: tuple[ToolCall, ...] = (), text: str = "") -> ModelResponse:
    return ModelResponse(
        text=text,
        tool_calls=tools,
        stop_reason="tool_use" if tools else "end",
        usage=_usage(),
    )


# ══════════════════════════════════════════════════════════════
#  路由：模型之后
# ══════════════════════════════════════════════════════════════


def test_tool_calls_route_to_tools() -> None:
    state: AgentGraphState = {
        "response": _response(tools=(ToolCall(id="1", name="write_file", input={}),))
    }
    assert route_after_model(state) == "tools"


def test_no_tool_calls_route_to_verify() -> None:
    """★ 模型说完事了不能直接结束 —— 必须先过自验。

    直接走 END 等于让模型自己宣布达标。
    """

    assert route_after_model({"response": _response(text="我做完了")}) == "verify"


def test_help_call_routes_to_help_not_tools() -> None:
    """★ 求助必须走独立分支。

    把 request_help 当普通工具执行，等于告诉模型「求助成功了，你继续」——
    实测后果是它每轮重复求助、12 轮烧完、一个文件没写。
    """

    state: AgentGraphState = {
        "response": _response(
            tools=(
                ToolCall(id="1", name="write_file", input={}),
                ToolCall(id="2", name="request_help", input={"reason": "x"}),
            )
        )
    }
    assert route_after_model(state) == "help", "求助与普通工具混在一起时，求助必须优先"


def test_pushback_without_response_routes_back_to_model() -> None:
    """★ 没有响应但也没有终局 = 本轮被回推了（如工具参数截断）。

    必须回到模型重来，**不能当成终局** ——
    把可恢复的情况判为终局，等于浪费一次本可成功的运行。
    """

    assert route_after_model({"response": None}) == "model"


def test_terminal_outcome_always_wins() -> None:
    """★ 一旦有终局原因，任何分支都不再走。"""

    state: AgentGraphState = {
        "outcome": "max_turns_exhausted",
        "response": _response(tools=(ToolCall(id="1", name="write_file", input={}),)),
    }
    assert route_after_model(state) == END


# ══════════════════════════════════════════════════════════════
#  路由：自验与求助之后
# ══════════════════════════════════════════════════════════════


def test_verify_pass_ends_but_fail_returns_to_model() -> None:
    """★ 谓词不过就回推 —— 这条边的全部意义。"""

    assert route_after_verify({"outcome": "completed"}) == END
    assert route_after_verify({}) == "model", "谓词不过却结束了，等于让模型自己签字"


def test_help_terminates_unless_pushed_back_once() -> None:
    assert route_after_help({"outcome": "help_requested"}) == END
    assert route_after_help({}) == "model"


# ══════════════════════════════════════════════════════════════
#  图的接线（用假节点，不碰模型）
# ══════════════════════════════════════════════════════════════


def _fake_graph(script: list[dict[str, Any]]):  # type: ignore[no-untyped-def]
    """按脚本逐轮返回模型响应的假图。"""

    visited: list[str] = []

    def model_node(state: AgentGraphState) -> dict[str, Any]:
        visited.append("model")
        turn = state.get("turn", 0)
        if turn >= len(script):
            return {"turn": turn + 1, "outcome": "max_turns_exhausted", "response": None}
        return {"turn": turn + 1, **script[turn]}

    def tools_node(state: AgentGraphState) -> dict[str, Any]:
        visited.append("tools")
        return {}

    def verify_node(state: AgentGraphState) -> dict[str, Any]:
        visited.append("verify")
        return {"outcome": "completed"}

    def help_node(state: AgentGraphState) -> dict[str, Any]:
        visited.append("help")
        return {"outcome": "help_requested", "detail": "需要人"}

    return build_agent_graph(
        model_node=model_node,
        tools_node=tools_node,
        verify_node=verify_node,
        help_node=help_node,
    ), visited


def test_tools_always_return_to_model() -> None:
    """★ 工具执行完必须回到模型 —— 结果不回传就退化成「一次性发命令」。"""

    graph, visited = _fake_graph(
        [
            {"response": _response(tools=(ToolCall(id="1", name="write_file", input={}),))},
            {"response": _response(text="done")},
        ]
    )
    graph.invoke({"messages": [], "transcript": [], "turn": 0}, {"recursion_limit": 20})

    assert visited == ["model", "tools", "model", "verify"], f"实际路径：{visited}"


def test_help_short_circuits_the_graph() -> None:
    """★ 求助终止：不再经过 verify，也不再回到 model。"""

    graph, visited = _fake_graph(
        [{"response": _response(tools=(ToolCall(id="1", name="request_help", input={"reason": "x"}),))}]
    )
    final = graph.invoke({"messages": [], "transcript": [], "turn": 0}, {"recursion_limit": 20})

    assert visited == ["model", "help"]
    assert final["outcome"] == "help_requested"


def test_messages_accumulate_rather_than_overwrite() -> None:
    """★ LangGraph 默认用返回值**覆盖**同名字段。

    消息历史必须追加 —— 覆盖会让每轮只剩最后一条，
    而那是**无声的丢失**：模型会失去全部上下文，却没有任何报错。
    """

    from codentum_contracts.interfaces import ModelMessage

    def model_node(state: AgentGraphState) -> dict[str, Any]:
        turn = state.get("turn", 0) + 1
        if turn > 3:
            return {"turn": turn, "outcome": "completed", "response": None}
        return {
            "turn": turn,
            "response": _response(tools=(ToolCall(id=str(turn), name="write_file", input={}),)),
            "messages": [ModelMessage(role="user", content=f"轮 {turn}")],
        }

    graph = build_agent_graph(
        model_node=model_node,
        tools_node=lambda s: {},
        verify_node=lambda s: {"outcome": "completed"},
        help_node=lambda s: {"outcome": "help_requested"},
    )
    final = graph.invoke({"messages": [], "transcript": [], "turn": 0}, {"recursion_limit": 30})

    contents = [m.content for m in final["messages"]]
    assert contents == ["轮 1", "轮 2", "轮 3"], f"消息被覆盖了：{contents}"
