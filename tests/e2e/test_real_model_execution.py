"""P0+ 判据：真实模型在控制平面内跑完一个 WorkPacket。

════════════════════════════════════════════════════════════════
 这一份补的是哪个空档
════════════════════════════════════════════════════════════════

`TestRealExecution`（同目录）证明的是「worker 真的改了代码并被验收」，
但它配的是 `RunnerConfig.command_runner(...)` —— 那个 worker 是一段
写死的 Python 脚本，**没有模型参与**。

而 B 的模型网关（provider / runner / smoke）有 20 项单测，
**全部使用假 gateway**；真实 API 只跑过手工 smoke，只证明「能连上」。

于是到 08-10 为止，「真实模型在 ReconcileLoop 里跑完一个 packet」
这件事一次都没发生过 —— 管道齐，水没通过。这一份就是那一步。

════════════════════════════════════════════════════════════════
 ⚠️ 关于 skip：跳过不是通过
════════════════════════════════════════════════════════════════

没有 API Key 时这一份会 skip。**skip 的绿灯什么都不证明** ——
这正是项目反复警告的「零输入的绿灯」（见 docs/项目进展与记忆.md §十七）。

所以：
  · skip 理由里写明「未执行」，不写「不适用」
  · 配了 Key 却失败时**大声失败**，绝不降级成 skip
  · 汇报时以「本机是否真的跑过」为准，不以「测试是否全绿」为准

跑它需要百炼 Key（任一环境变量）：

    DASHSCOPE_API_KEY / BAILIAN_API_KEY / QWEN_API_KEY / AGENTTEAMS_LLM_API_KEY

════════════════════════════════════════════════════════════════
 ★ 这一份**不**证明什么
════════════════════════════════════════════════════════════════

`ModelGatewayRunner` 目前是 **one-shot 模型调用，没有工具循环** ——
模型返回文本，runner 把它写成证据，**不会去改任何文件**。

所以这一份证明的是：
    真实模型被真的调用了 → 用量/成本被记账 → 产出被当作真实证据 → packet 走完验收

它**不**证明「模型改了代码」。那条判据仍然由 `TestRealExecution`
（command_runner 那条）守着。两条合起来才是完整的 P0。

把这段写在这里，是因为最容易发生的误读就是把这一份的绿灯说成
「模型已经能自己写代码了」。

════════════════════════════════════════════════════════════════
 ★★ 第一次真跑就暴露了两个缺陷（2026-08-10）
════════════════════════════════════════════════════════════════

模型返回的不是代码，是一份 **blocker 报告**：

    "The visible context does not provide any specific details about the
     task or the changes that need to be made."

**模型是对的。** 看一眼实际发出去的 prompt（`docs/experiments/real-model-run/
prompt-user.md`）就明白：`Visible Context: (none)`，全文没有一句「要做什么」。

缺陷一 —— **契约里没有「要做什么」这个字段。**
`WorkPacket` 的字段是 id / kind / state / role / ownsPaths / readsPaths /
deps / acceptance / budget / routing / attempts / evidence / provenance。
任务意图只能靠 `ContextBundle` 注入，而它是可选的：没配 `context_loader`
就是空。command_runner 那条 e2e 看不出这一点 —— 那个 worker 是写死的脚本，
它根本不需要知道任务是什么。**换成真模型，缺口立刻现形。**

缺陷二 —— **worker 自陈干不了，系统照样验收。**
`stop_reason=end` 且没有 tool_calls → runner 判 `status=completed` →
`WorkerCompleted` → 验收看到一条非 sys: 的真实证据 → `accepted`。
于是「我做不了这个任务」这份报告，被当成了交付物。

这正是 §十五 推论 2 说的那件事：**「可判定」不等于「判得出差别」。**
一条永远返回 true 的验收谓词也是机器可判定的。

下面 `test_blocker_report_should_not_be_accepted` 用 xfail(strict=True) 钉住
缺陷二 —— 它现在**预期失败**；一旦有人把它修好，strict 会让它变红，
提醒把这条 xfail 摘掉。**不用普通断言，是因为那会把缺陷写成"预期行为"。**
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from codentum_contracts.state import PacketId, WorkPacket, dump_state
from codentum_control_plane.budget import BudgetTracker
from codentum_control_plane.reconcile import ReconcileLoop
from codentum_harness.runtime import (
    LocalWorkerRuntimeConfig,
    ModelGatewayConfig,
    RunnerConfig,
    build_local_worker_runtime,
)

#: 与 B 的 provider 实现读取顺序一致
_KEY_ENVS = (
    "DASHSCOPE_API_KEY",
    "BAILIAN_API_KEY",
    "QWEN_API_KEY",
    "AGENTTEAMS_LLM_API_KEY",
)

#: RoleSpec `coder` 的默认模型（08-09 按账号实测改成的可调固定版本）
_CODER_MODEL = "qwen-coder-plus-1106"


def _key_env() -> str | None:
    for name in _KEY_ENVS:
        if os.environ.get(name):
            return name
    return None


requires_key = pytest.mark.skipif(
    _key_env() is None,
    reason=(
        "★ 未执行（不是通过）：本机没有百炼 Key。"
        f"配置 {' / '.join(_KEY_ENVS)} 任一后重跑。"
    ),
)


def _git_init(path: Path) -> None:
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "e2e@codentum.local"],
        ["git", "config", "user.name", "codentum-e2e"],
    ):
        subprocess.run(cmd, cwd=path, check=True, capture_output=True)
    (path / ".gitkeep").write_text("", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "e2e base"], cwd=path, check=True, capture_output=True
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    return repo


def _packet(pid: str) -> WorkPacket:
    return WorkPacket(
        id=PacketId(pid),
        kind="impl",
        state="pending",
        role="coder",
        ownsPaths=("src/app/",),
        readsPaths=("tests/",),
        deps=(),
        acceptance={"kind": "test", "predicate": "pytest", "authoredBy": "qa"},
        budget={
            "currency": "CNY",
            "limitCny": 1.0,
            "spentCny": 0.0,
            "degradationChain": ("drop_semantic",),
        },
        # ★ 不填 routing 的话，A 的 _build_spawn_request 会退回 model="default"，
        #   而 "default" 在百炼那边不存在 —— 会以 provider 报错收场，
        #   看上去像"模型调不通"，真因却是路由没填。
        routing={"model": _CODER_MODEL, "effort": "medium"},
        attempts=0,
        evidence=(),
        provenance={"createdBy": "planner", "createdAt": "2026-08-10T00:00:00Z"},
    )


@requires_key
def test_real_model_completes_a_packet(project: Path) -> None:
    state_dir = project / ".codentum"
    (state_dir / "packets").mkdir(parents=True)
    pkt = _packet("wp-rm0001")
    (state_dir / "packets" / f"{pkt.id}.json").write_text(
        json.dumps(dump_state(pkt), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    loop = ReconcileLoop(
        state_dir=str(state_dir),
        budget_tracker=BudgetTracker(limit_cny=5.0),
    )
    loop.load_state()
    loop.worker_runtime = build_local_worker_runtime(
        LocalWorkerRuntimeConfig(
            repo_root=project,
            runner=RunnerConfig.model_gateway(
                ModelGatewayConfig.bailian(
                    pricing={},
                    api_key_env=_key_env(),
                    # ★ 价格表证据尚未落地（待办 7d）。这里显式放行 unknown pricing，
                    #   代价是**成本数字不能当证据** —— 下面只断言"记了账"，
                    #   不断言"金额对"。把它写成 False 而不是偷偷传个假价格。
                    require_pricing=False,
                ),
                timeout_seconds=180.0,
            ),
        )
    )

    report = loop.run_until_stable(max_ticks=30)
    loop.save_state()
    trace = "\n  ".join(
        f"{t.from_state} -> {t.to_state} | {t.detail}" for t in report.transitions
    )
    final = loop.packet(PacketId("wp-rm0001"))

    # 1) 走完全程并被验收
    assert final.state == "accepted", f"没走到 accepted。轨迹：\n  {trace}"

    # 2) 验收依据必须是真实证据，不能是控制面自己的簿记
    real = [e for e in final.evidence if not e.startswith("sys:")]
    assert real, f"验收了，但没有一条真实证据：{list(final.evidence)}"

    # 3) 模型确实被调用过 —— 用量落盘且非零
    workspace = project.parent / "codentum-workers" / "wp-rm0001" / "attempt-1"
    result_files = list(workspace.rglob(".codentum/evidence/*/model/result.json"))
    assert result_files, f"没有模型证据目录。轨迹：\n  {trace}"
    result = json.loads(result_files[0].read_text(encoding="utf-8"))

    # ★ 用量不在 result.json 里，而在同目录的 usage.json —— result.json 只留了
    #   `usage_path` 指针。第一版断言读错了文件，红了一次才发现；把路径写死在
    #   这里而不是猜，是为了让"读错文件"不会伪装成"模型没被调用"。
    usage_file = result_files[0].parent / result["usage_path"]
    assert usage_file.exists(), f"result.json 指向的 usage 文件不存在：{usage_file}"
    usage = json.loads(usage_file.read_text(encoding="utf-8"))

    assert usage.get("input_tokens", 0) > 0, f"input_tokens 不是正数：{usage}"
    assert usage.get("output_tokens", 0) > 0, f"output_tokens 不是正数：{usage}"

    # 4) 记账字段在（金额本身不作为证据 —— 价格表未落地，见待办 7d）
    assert "cost_cny" in usage, f"用量里没有 cost_cny 字段：{usage}"
    assert "spent_cny" in result, f"result.json 没有记账字段：{sorted(result)}"

    # 5) 用的是路由表指定的模型，不是某个默认值
    assert result.get("model") == _CODER_MODEL, (
        f"实际调用的模型不是路由表里的 {_CODER_MODEL}：{result.get('model')!r}"
    )


@requires_key
def test_model_response_is_not_empty(project: Path) -> None:
    """模型返回空文本也能走到 accepted 的话，这条链路就是空转的。

    ★ 单独一条，因为上一条即使模型返回空字符串也会全绿：
      证据文件在、usage 非零、状态到 accepted —— 一样不缺。
      「有回应」和「回应有内容」是两件事。
    """
    state_dir = project / ".codentum"
    (state_dir / "packets").mkdir(parents=True)
    pkt = _packet("wp-rm0002")
    (state_dir / "packets" / f"{pkt.id}.json").write_text(
        json.dumps(dump_state(pkt), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    loop = ReconcileLoop(
        state_dir=str(state_dir), budget_tracker=BudgetTracker(limit_cny=5.0)
    )
    loop.load_state()
    loop.worker_runtime = build_local_worker_runtime(
        LocalWorkerRuntimeConfig(
            repo_root=project,
            runner=RunnerConfig.model_gateway(
                ModelGatewayConfig.bailian(
                    pricing={}, api_key_env=_key_env(), require_pricing=False
                ),
                timeout_seconds=180.0,
            ),
        )
    )
    loop.run_until_stable(max_ticks=30)

    workspace = project.parent / "codentum-workers" / "wp-rm0002" / "attempt-1"
    texts = list(workspace.rglob(".codentum/evidence/*/model/response.txt"))
    assert texts, "没有 response.txt"
    body = texts[0].read_text(encoding="utf-8").strip()
    assert len(body) > 20, f"模型回应几乎是空的（{len(body)} 字符）：{body!r}"


@requires_key
@pytest.mark.xfail(
    strict=True,
    reason=(
        "★ 已知缺陷（2026-08-10 首次真跑发现）：worker 自陈 blocker 仍被验收。"
        "runner 按 stop_reason=end 判 completed，验收只看「有非 sys: 证据」，"
        "于是「我做不了」这份报告成了交付物。"
        "修法需要设计决定（验收谓词要能判出差别 / 契约要不要加任务描述字段），"
        "不是一处补丁 —— 见 docs/项目进展与记忆.md 待办 22。"
        "strict=True：修好之后这条会变红，提醒摘掉 xfail。"
    ),
)
def test_blocker_report_should_not_be_accepted(project: Path) -> None:
    """模型明确说「上下文不足、干不了」时，packet 不该走到 accepted。"""
    state_dir = project / ".codentum"
    (state_dir / "packets").mkdir(parents=True)
    pkt = _packet("wp-rm0003")
    (state_dir / "packets" / f"{pkt.id}.json").write_text(
        json.dumps(dump_state(pkt), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    loop = ReconcileLoop(
        state_dir=str(state_dir), budget_tracker=BudgetTracker(limit_cny=5.0)
    )
    loop.load_state()
    loop.worker_runtime = build_local_worker_runtime(
        LocalWorkerRuntimeConfig(
            repo_root=project,
            runner=RunnerConfig.model_gateway(
                ModelGatewayConfig.bailian(
                    pricing={}, api_key_env=_key_env(), require_pricing=False
                ),
                timeout_seconds=180.0,
            ),
        )
    )
    loop.run_until_stable(max_ticks=30)

    # 这个 packet 没有任何任务描述（契约里也没有能放它的字段），
    # 模型只能报 blocker。那么它不该被判为「干完了」。
    final = loop.packet(PacketId("wp-rm0003"))
    assert final.state != "accepted", (
        "worker 报了 blocker 却被验收 —— 「我做不了」被当成了交付物"
    )
