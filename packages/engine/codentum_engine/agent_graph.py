"""执行平面的 LangGraph 控制流图 —— 按 ADR-0004 落地。

════════════════════════════════════════════════════════════════
 ★ 为什么这里用 LangGraph，而控制平面不用
════════════════════════════════════════════════════════════════

设计文档里有「四张图」，LangGraph 也叫 Graph，**两者不是同一个东西**：

| | 是什么 | 例子 |
|---|---|---|
| 四张图 | **数据结构** —— 系统状态的拓扑 | 依赖图（谁等谁）、所有权图（哪条路径被谁锁） |
| LangGraph | **控制流图** —— 一次执行的步骤走向 | 节点是函数，边是转移 |

依赖图不是可执行的图，它是一张「谁要等谁」的表 —— 拿 LangGraph 表达它是范畴错误。
而**一次 worker 执行内部的 推理 → 用工具 → 自验 → 收敛 循环，正是控制流图**。

ADR-0004 的决策：**LangGraph 用在执行平面，控制平面保持自研的确定性调和循环。**
控制平面不用的三条理由（零 LLM、状态存 Git 与 checkpointer 打架、
幂等性交给框架更难论证）在那份 ADR 里有完整论证。

════════════════════════════════════════════════════════════════
 ★ 这次改造是「行为等价重构」，不是重写
════════════════════════════════════════════════════════════════

改造前是 `agent_runner.py` 里的手写 `for turn in range(...)` 循环。
它跑得通，但**与 ADR-0004 的设计不符** —— 而 `langgraph` 一直躺在
`pyproject.toml` 的依赖里、README 也写着「执行平面采用 LangGraph 编排」，
实际零 import。

> 一个装了却没用的依赖，比没有这个依赖更糟 ——
> 它让读者以为系统有 checkpointer、有流式、有 human-in-the-loop。

★ 判据：`test_agent_runner.py` 里那 13 条测试**一条都不改**。
  它们定义了循环必须保持的语义（工具结果回传、撞上限不伪装成完成、
  求助终止、自验与门禁同判据、瞬时错误重试而确定性错误不重试……）。
  能在不改测试的前提下换掉实现，才说明这是重构而不是改行为。

════════════════════════════════════════════════════════════════
 ★ 图的形状
════════════════════════════════════════════════════════════════

                    ┌──────────┐
        ┌──────────►│  model   │◄─────────────┐
        │           └────┬─────┘              │
        │                │                    │
        │      ┌─────────┼──────────┐         │
        │      │         │          │         │
        │  tool_calls  help    no tool_calls  │
        │      │         │          │         │
        │      ▼         ▼          ▼         │
        │  ┌───────┐ ┌──────┐  ┌────────┐    │
        └──┤ tools │ │ help │  │ verify │────┘  谓词不过 → 回推
           └───────┘ └──┬───┘  └───┬────┘
                        │          │ 谓词通过
                     终止│          ▼
                        └────────►[END]

★ 每个节点都是**纯函数式的状态转换**，副作用（写文件、调模型）集中在
  节点内部，条件边只读状态 —— 这让「下一步走哪」可单独测试。
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, StateGraph

# ★ `END` 是 LangGraph 的运行时常量，mypy 无法推断它等于 "__end__"。
#   这里显式标注为 str 供路由函数返回 —— 不用 Literal 硬编码它的值，
#   否则框架若改常量，类型层面会「看起来正确」而运行时错。
_END: str = END

from codentum_contracts.interfaces import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelSession,
)

__all__ = ["AgentGraphState", "build_agent_graph"]

logger = logging.getLogger(__name__)


def _append(left: list[Any], right: list[Any]) -> list[Any]:
    """状态合并器：列表追加而非覆盖。

    ★ LangGraph 默认用返回值**覆盖**同名字段。消息历史与轨迹必须追加 ——
      覆盖会让每轮只剩最后一条，而那是无声的丢失。
    """

    return [*left, *right]


class AgentGraphState(TypedDict, total=False):
    """一次 worker 执行的控制流状态。

    ★ 与 `.codentum/` 里的**系统状态**严格区分：这里的 state 是
      「本次执行的临时状态」，随进程结束而消失；系统状态在磁盘上，
      由控制平面负责。ADR-0004 特意强调过这条边界 ——
      让 LangGraph 的 checkpointer 管系统状态，会与「Git 是唯一状态源」打架。
    """

    messages: Annotated[list[ModelMessage], _append]
    transcript: Annotated[list[dict[str, Any]], _append]
    turn: int
    response: ModelResponse | None
    outcome: str | None
    """终局原因：completed / help_requested / max_turns_exhausted / model_error"""
    detail: str
    helped_once: bool


# ── 路由判定（纯函数，可单独测试）────────────────────────────


def route_after_model(state: AgentGraphState) -> str:
    """模型返回后走哪条边。

    ★ 这是纯函数：只读 state，不产生副作用。
      「下一步走哪」因此可以脱离模型与文件系统单独测试 ——
      而在手写 for 循环里，这个判断和副作用是混在一起的。
    """

    if state.get("outcome"):
        return _END
    response = state.get("response")
    if response is None:
        # ★ 没有响应但也没有终局 = 本轮被回推了（如工具参数截断），
        #   回到模型重来 —— **不能当成终局**。
        #   把可恢复的情况判为终局，等于浪费一次本可成功的运行。
        return "model"
    if not response.tool_calls:
        return "verify"
    if any(call.name == "request_help" for call in response.tool_calls):
        return "help"
    return "tools"


def route_after_verify(state: AgentGraphState) -> str:
    """自验之后：谓词通过则收敛，不过则回到模型。

    ★ 「不过就回推」是这条边的全部意义 —— 模型说「我做完了」不算数，
      验收谓词才算。让模型自己宣布达标，等于让它自己给自己签字。
    """

    return _END if state.get("outcome") else "model"


def route_after_help(state: AgentGraphState) -> str:
    """求助之后：首次给一次事实性回推，再次即终止。

    ★ 求助的语义是「我需要人来决定」，不该在求助后还让模型自己往下猜。
      但首次求助常常只是「不知道验收标准」，而验收标准一直在 prompt 里 ——
      把谓词的真实输出摆给它看，比重复一遍要求有效。
    """

    return _END if state.get("outcome") else "model"


# ── 图的装配 ────────────────────────────────────────────────


def build_agent_graph(
    *,
    model_node: Any,
    tools_node: Any,
    verify_node: Any,
    help_node: Any,
) -> Any:
    """按 ADR-0004 装配执行平面的控制流图。

    四个节点由调用方注入（它们需要 session / 工具执行器 / 谓词，
    那些是执行平面的资源）——**图的形状与节点的实现分离**，
    因此图本身可以用假节点单独测试。
    """

    graph = StateGraph(AgentGraphState)

    graph.add_node("model", model_node)
    graph.add_node("tools", tools_node)
    graph.add_node("verify", verify_node)
    graph.add_node("help", help_node)

    graph.set_entry_point("model")

    graph.add_conditional_edges(
        "model",
        route_after_model,
        {"tools": "tools", "help": "help", "verify": "verify", "model": "model", END: END},
    )
    # 工具执行完无条件回到模型 —— 结果必须回传，否则退化成「一次性发命令」
    graph.add_edge("tools", "model")
    graph.add_conditional_edges("verify", route_after_verify, {"model": "model", END: END})
    graph.add_conditional_edges("help", route_after_help, {"model": "model", END: END})

    return graph.compile()
