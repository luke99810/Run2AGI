"""会真的执行验收谓词的门禁 —— 判据。

★ 这一层补的是 §二十二 那条线的第四层：

    08-09  拿控制面自己的簿记当证据
    08-10  门禁层同一个洞
    08-11  说完成了但一个文件没改
    08-12  **改了文件，但没达到验收标准**

前三层都能靠「看有没有 X」解决，这一层必须**真的把谓词跑一遍**。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from codentum_contracts.state import (
    Acceptance,
    BudgetGrant,
    EvidenceRef,
    ModelRouting,
    PacketId,
    Provenance,
    WorkPacket,
)
from codentum_engine.acceptance import build_executing_acceptance_gate


def _packet(
    *,
    kind: str = "test",
    predicate: str,
    evidence: tuple[EvidenceRef, ...] = (EvidenceRef("file:model/result.json"),),
    attempts: int = 1,
    pid: str = "wp-acc000001",
) -> WorkPacket:
    return WorkPacket(
        id=PacketId(pid),
        kind="impl",
        state="review",
        role="coder",
        ownsPaths=("workspace/",),
        readsPaths=(),
        deps=(),
        acceptance=Acceptance(
            kind=kind,  # type: ignore[arg-type]
            predicate=predicate,
            threshold=None,
            authoredBy="qa",
        ),
        budget=BudgetGrant(
            currency="CNY", limitCny=1.0, spentCny=0.0, degradationChain=("drop_semantic",)
        ),
        routing=ModelRouting(model="m", effort="medium", batch=None),
        attempts=attempts,
        evidence=evidence,
        provenance=Provenance(createdBy="intake", createdAt="2026-08-12T00:00:00Z", parent=None),
    )


@pytest.fixture
def workers_root(tmp_path: Path) -> Path:
    root = tmp_path / "codentum-workers"
    (root / "wp-acc000001" / "attempt-1").mkdir(parents=True)
    return root


# ══════════════════════════════════════════════════════════════
#  第四层：谓词真的被执行
# ══════════════════════════════════════════════════════════════


def test_failing_predicate_blocks_acceptance(workers_root: Path) -> None:
    """★ 这是整层要证明的事：证据齐全、文件也改了，但**验收谓词跑不过** → 不得验收。

    2026-08-12 之前，这种 packet 会被判 accepted，因为门禁只看「有没有证据」。
    """

    gate = build_executing_acceptance_gate(workers_root)
    verdict = gate(_packet(predicate=f"{sys.executable} -c \"raise SystemExit(1)\""))

    assert verdict.passed is False
    assert "退出码 1" in verdict.detail


def test_passing_predicate_allows_acceptance(workers_root: Path) -> None:
    """★ 对照组。没有它，上面那条用「永远不通过」也能绿。"""

    gate = build_executing_acceptance_gate(workers_root)
    verdict = gate(_packet(predicate=f"{sys.executable} -c \"print('ok')\""))

    assert verdict.passed is True
    assert "退出码 0" in verdict.detail


def test_predicate_runs_inside_the_worker_workspace(workers_root: Path) -> None:
    """★ 谓词必须在 **worker 的工作区**里跑，否则它验的是别人的代码。"""

    (workers_root / "wp-acc000001" / "attempt-1" / "produced.py").write_text("x = 1", encoding="utf-8")
    gate = build_executing_acceptance_gate(workers_root)
    verdict = gate(
        _packet(predicate=f"{sys.executable} -c \"import pathlib,sys; sys.exit(0 if pathlib.Path('produced.py').exists() else 1)\"")
    )

    assert verdict.passed is True, verdict.detail


def test_latest_attempt_is_what_gets_verified(tmp_path: Path) -> None:
    """★ 重试时验的必须是**刚跑完的那一次**，不是第一次。

    验旧 attempt 的后果最难查：改对了却一直不通过，而日志看起来一切正常。
    """

    root = tmp_path / "codentum-workers"
    (root / "wp-acc000001" / "attempt-1").mkdir(parents=True)
    (root / "wp-acc000001" / "attempt-2").mkdir(parents=True)
    (root / "wp-acc000001" / "attempt-2" / "fixed.py").write_text("ok", encoding="utf-8")

    gate = build_executing_acceptance_gate(root)
    verdict = gate(
        _packet(
            predicate=f"{sys.executable} -c \"import pathlib,sys; sys.exit(0 if pathlib.Path('fixed.py').exists() else 1)\"",
            attempts=2,
        )
    )
    assert verdict.passed is True, verdict.detail


# ══════════════════════════════════════════════════════════════
#  前三层仍然有效
# ══════════════════════════════════════════════════════════════


def test_worker_failure_marker_still_blocks(workers_root: Path) -> None:
    gate = build_executing_acceptance_gate(workers_root)
    verdict = gate(
        _packet(
            predicate=f"{sys.executable} -c \"pass\"",
            evidence=(EvidenceRef("sys:worker-failed:wp-acc000001:acceptance_not_met"),),
        )
    )
    assert verdict.passed is False
    assert "明确失败过" in verdict.detail


def test_sys_bookkeeping_alone_still_blocks(workers_root: Path) -> None:
    gate = build_executing_acceptance_gate(workers_root)
    verdict = gate(
        _packet(
            predicate=f"{sys.executable} -c \"pass\"",
            evidence=(EvidenceRef("sys:lock:wp-acc000001:1"),),
        )
    )
    assert verdict.passed is False
    assert "sys:" in verdict.detail


# ══════════════════════════════════════════════════════════════
#  说清楚自己没做什么
# ══════════════════════════════════════════════════════════════


def test_non_test_predicate_says_it_was_not_executed(workers_root: Path) -> None:
    """★ manual / metric 类谓词跑不了，必须**如实说明**而不是默默放行。

    放行本身保持既有行为，但 detail 里要写清「这一条没有被执行过」——
    否则下游看到 passed=True 会以为验收真的过了。
    """

    gate = build_executing_acceptance_gate(workers_root)
    verdict = gate(_packet(kind="manual", predicate="operator-review: 需人工判定"))

    assert verdict.passed is True
    assert "未被执行" in verdict.detail


def test_missing_workspace_fails_loudly(tmp_path: Path) -> None:
    """★ 找不到工作区要报失败，不能当成「验收通过」。"""

    gate = build_executing_acceptance_gate(tmp_path / "nowhere")
    verdict = gate(_packet(predicate=f"{sys.executable} -c \"pass\""))

    assert verdict.passed is False
    assert "找不到" in verdict.detail


def test_the_executing_gate_is_registered_under_the_id_the_loop_actually_uses(
    tmp_path: Path,
) -> None:
    """★ `_try_review_to_accepted` 在没有 transition_table 时用的 gate_id 是
    **"review"**，不是 "acceptance"。

    2026-08-12 实测：只注册 acceptance 时，日志写「门禁 'review' 通过」、
    packet 被 accepted，而验收谓词一次都没跑过。

    ★ 判据装了但没接上，比没装更糟 —— 它让人以为已经在判了。
    """

    import subprocess

    from codentum_engine.service import EngineConfig, EngineService

    project = tmp_path / "repo"
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True, capture_output=True)

    service = EngineService(EngineConfig(project_root=project))
    loop = service._build_loop()  # noqa: SLF001

    for gate_id in ("acceptance", "review"):
        verdict = loop.gate_runner.check(  # type: ignore[union-attr]
            gate_id,
            _packet(predicate="python -c \"raise SystemExit(1)\"", pid="wp-notreg001"),
        )
        assert verdict.passed is False, f"门禁 {gate_id!r} 没有执行谓词就放行了"


# ══════════════════════════════════════════════════════════════
#  第五层：验收测试本身不能是空的
# ══════════════════════════════════════════════════════════════


def test_vacuous_tests_do_not_pass_acceptance(workers_root: Path) -> None:
    """★ 2026-08-12 真实撞到的那一个：实现是对的，测试只有一句 `assert True`。

    验收谓词 `pytest workspace -q` 返回 1 passed、退出码 0，packet 被 accepted ——
    **验收标准达到了，而验收标准本身什么都没验证。**

    判据是「如果它坏了，哪条测试会变红」。这里的答案是：一条都不会。
    """

    ws = workers_root / "wp-acc000001" / "attempt-1"
    (ws / "subscriptions.py").write_text(
        "def monthly_total(subs):\n    return sum(s for s in subs)\n", encoding="utf-8"
    )
    # ★ 原样照抄模型当时写出来的东西
    (ws / "test_subscriptions.py").write_text(
        "def test_example():\n    assert True\n", encoding="utf-8"
    )

    gate = build_executing_acceptance_gate(workers_root)
    verdict = gate(_packet(predicate=f"{sys.executable} -m pytest . -q"))

    assert verdict.passed is False, "空测试通过了验收"
    assert "验收测试是空的" in verdict.detail
    # ★ 工作区必须被还原 —— 检查本身不能留下残骸
    assert (ws / "subscriptions.py").exists()
    assert not list(ws.glob("*.vacuity-check"))


def test_real_tests_still_pass_acceptance(workers_root: Path) -> None:
    """★ 对照组。没有它，上面那条用「永远不通过」也能绿。

    真正验证实现的测试：实现被移走后会 ImportError，因此必然变红。
    """

    ws = workers_root / "wp-acc000001" / "attempt-1"
    (ws / "subscriptions.py").write_text(
        "def monthly_total(subs):\n    return sum(subs)\n", encoding="utf-8"
    )
    (ws / "test_subscriptions.py").write_text(
        "from subscriptions import monthly_total\n\n\n"
        "def test_empty():\n    assert monthly_total([]) == 0\n\n\n"
        "def test_sum():\n    assert monthly_total([1.5, 2.5]) == 4.0\n",
        encoding="utf-8",
    )

    gate = build_executing_acceptance_gate(workers_root)
    verdict = gate(_packet(predicate=f"{sys.executable} -m pytest . -q"))

    assert verdict.passed is True, verdict.detail
    assert "移走后确实变红" in verdict.detail
    assert (ws / "subscriptions.py").exists()


def test_workspace_is_restored_even_when_the_predicate_explodes(workers_root: Path) -> None:
    """★ 还原必须在 finally 里 —— 检查过程中出任何事都不能把工作区留成残破状态。

    验收判错可以重来；把用户的文件弄丢不能。
    """

    ws = workers_root / "wp-acc000001" / "attempt-1"
    (ws / "impl.py").write_text("x = 1", encoding="utf-8")
    (ws / "test_impl.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")

    gate = build_executing_acceptance_gate(workers_root)
    gate(_packet(predicate=f"{sys.executable} -m pytest . -q"))

    assert (ws / "impl.py").read_text(encoding="utf-8") == "x = 1"
    assert not list(ws.glob("*.vacuity-check"))
