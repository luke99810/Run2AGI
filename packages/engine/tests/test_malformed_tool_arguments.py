"""工具参数畸形 —— 两种形态都要能自我纠正。

★ 这组守的是一条被**真机跑**抓出来的缺陷。

  2026-08-16 第一次真机执行：前三轮正常（写文件、跑测试），
  第四轮模型给出的 `arguments` 解得开但不是对象，于是整次执行作废。

  原因不是解析器太严，而是 `agent_runner` 的回推恢复靠
  `"JSON object text" in str(exc)` 这个**子串判断**分支 ——
  它只覆盖「被截断」那一种。

★ **靠错误消息的措辞来分支，是一改文案就断的耦合。**
  改文案不会有任何测试变红，而恢复能力会静默消失。
  所以生产代码改成按**类型**分支，这组测试也按类型构造。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from codentum_contracts.interfaces import (
    BudgetGrantRuntime,
    ModelRequest,
    ModelResponse,
    SpawnRequest,
    Usage,
)
from codentum_contracts.state import ModelRouting, PacketId
from codentum_engine.agent_runner import AgentRunnerConfig, build_agent_runner
from codentum_harness.model_gateway import MalformedToolArgumentsError
from codentum_harness.prompt_bundle import write_worker_prompt_bundle
from codentum_roles.loader import load_builtin_role_specs


def _usage() -> Usage:
    return Usage(input_tokens=10, output_tokens=10, cached_input_tokens=0, cost_cny=0.0)


def _spawn(workspace: Path) -> SpawnRequest:
    return SpawnRequest(
        packet_id=PacketId("wp-malform01"),
        role="coder",
        mounts=(),
        tools=("write_file", "read_file"),
        routing=ModelRouting(model="fake-model", effort="medium"),
        budget=BudgetGrantRuntime(limit_cny=1.0, degradation_chain=()),
        workspace=str(workspace),
        attempt=1,
    )


@pytest.fixture
def prepared(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    evidence = workspace / ".codentum" / "evidence" / "wp-malform01-attempt-1"
    evidence.mkdir(parents=True)
    write_worker_prompt_bundle(
        request=_spawn(workspace),
        role_spec=next(spec for spec in load_builtin_role_specs() if spec.id == "coder"),
        evidence_dir=evidence,
    )
    return workspace


class _Session:
    """先抛预置异常，之后正常收尾。"""

    session_id = "fake"
    model = "fake-model"
    role = "coder"

    def __init__(self, errors: list[Exception]) -> None:
        self._errors = errors
        self.seen: list[ModelRequest] = []

    async def invoke(self, req: ModelRequest) -> ModelResponse:
        self.seen.append(req)
        if self._errors:
            raise self._errors.pop(0)
        return ModelResponse(text="改好了", tool_calls=(), stop_reason="end", usage=_usage())

    def stream(self, req: ModelRequest) -> Any:  # pragma: no cover - 未使用
        raise NotImplementedError

    def spent_cny(self) -> float:
        return 0.0

    async def close(self) -> None:
        return None


class _Gateway:
    def __init__(self, errors: list[Exception]) -> None:
        self.session = _Session(errors)

    async def open(self, role: str, routing: ModelRouting, grant_cny: float) -> _Session:
        return self.session

    async def estimate(self, routing: ModelRouting, req: ModelRequest) -> Any:  # pragma: no cover
        raise NotImplementedError

    async def ledger(self) -> Any:  # pragma: no cover
        raise NotImplementedError


TRUNCATED = "response field 'arguments' must be JSON object text; got '{\"path\": \"a.py'"
NOT_AN_OBJECT = "response field 'arguments' must be a JSON object; got str '写完了'"


@pytest.mark.parametrize("message", [TRUNCATED, NOT_AN_OBJECT])
def test_both_malformed_shapes_are_recovered(prepared: Path, message: str) -> None:
    """★ 两种畸形都由模型自己能纠正，都该回推重写而不是整次作废。

    第二种（解得开但不是对象）此前**不触发恢复** —— 真机第一次跑就撞上，
    前三轮已经写好文件、跑过测试的一次执行整个作废。
    """

    gateway = _Gateway([MalformedToolArgumentsError(message)])
    runner = build_agent_runner(AgentRunnerConfig(gateway=gateway))  # type: ignore[arg-type]
    outcome = runner(_spawn(prepared))

    # ★ 判据是「有没有重试」，不是「最终成没成功」。
    #   这个假模型恢复之后并不写文件，所以最终**理应**判失败
    #   （「说完事了却一个文件都没写」是另一条判据在管）。
    #   把两件事混在一个断言里，这条测试会因为无关的原因红/绿。
    assert len(gateway.session.seen) >= 2, "没有重试 —— 恢复分支没走到"
    detail = getattr(outcome, "detail", "")
    assert "参数无法解析" not in detail, f"最终失败原因仍是畸形参数，说明没恢复：{detail}"


def test_pushback_carries_the_actual_reason(prepared: Path) -> None:
    """★ 回推必须带上**实际的**错误内容。

    只说「参数有问题」而不说哪里有问题，模型只能重试同样的东西 ——
    那就不是自我纠正，是重复撞墙。
    """

    gateway = _Gateway([MalformedToolArgumentsError(NOT_AN_OBJECT)])
    runner = build_agent_runner(AgentRunnerConfig(gateway=gateway))  # type: ignore[arg-type]
    runner(_spawn(prepared))

    pushback = "\n".join(m.content for m in gateway.session.seen[-1].messages)
    assert "参数无法解析" in pushback
    assert "got str" in pushback, "没把实际拿到的东西告诉模型"


def test_recovery_stops_at_the_turn_limit(prepared: Path) -> None:
    """★ 恢复不能无限循环 —— 一直畸形就该如实失败。

    无限回推会把一次坏掉的执行变成一次昂贵的坏掉的执行。
    """

    gateway = _Gateway([MalformedToolArgumentsError(NOT_AN_OBJECT) for _ in range(10)])
    runner = build_agent_runner(AgentRunnerConfig(gateway=gateway, max_turns=3))  # type: ignore[arg-type]
    outcome = runner(_spawn(prepared))

    assert outcome.status == "failed"
    assert len(gateway.session.seen) <= 4, "回推次数没有被轮数上限约束"


def test_the_error_type_is_what_production_branches_on() -> None:
    """★ 生产代码必须按**类型**分支，不按错误消息的措辞。

    这条直接查源码：一旦有人改回子串判断，恢复能力会在下一次
    文案调整时静默消失，而不会有任何测试变红 —— 除了这一条。
    """

    import ast

    source = (
        Path(__file__).resolve().parents[1] / "codentum_engine" / "agent_runner.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    # ★ 走 AST 而不是字符串匹配：第一版直接搜 `"JSON object text" not in str(exc)`，
    #   结果匹配到了**生产代码里解释这段历史的注释**，测试自己红了。
    #   这与 check_docker 的凭证规则是同一条教训：
    #   **判据要认语义，不要认字符串** —— 注释里提到某个写法是正常的。
    handlers = [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)]
    caught = {
        ast.unparse(h.type)
        for h in handlers
        if h.type is not None
    }
    assert "MalformedToolArgumentsError" in caught, f"恢复没有按类型捕获；捕获的是 {caught}"

    # 反向：不许再出现「拿错误消息内容做分支」的比较
    offenders = [
        ast.unparse(n)
        for n in ast.walk(tree)
        if isinstance(n, ast.Compare)
        and "str(exc)" in ast.unparse(n)
        and any(isinstance(op, (ast.In, ast.NotIn)) for op in n.ops)
    ]
    assert not offenders, f"又退回按错误消息措辞分支了：{offenders}"
