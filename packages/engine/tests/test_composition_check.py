"""算子二：集成谓词的桩化检验。

★ 这组测试守的是**能不能区分**，而不是「能不能跑」。
  一个永远返回「全部覆盖」的检查也能跑，而且看起来一切正常 ——
  所以必须同时钉住两个方向：真集成放行、假集成拦下。
"""

from __future__ import annotations

import sys
from pathlib import Path

from codentum_engine.acceptance import composition_check

# ══════════════════════════════════════════════════════════════
#  一个最小的双模块工作区
#
#  alpha 提供 add()，beta 提供 fmt()。
#  「集成测试」只用到 beta —— 于是 alpha 应当被判为**未覆盖**。
# ══════════════════════════════════════════════════════════════

_ALPHA_SRC = """
def add(a, b):
    return a + b
"""

_ALPHA_TEST = """
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from alpha.src.calc import add


def test_add():
    assert add(1, 2) == 3
"""

_BETA_SRC = """
def fmt(n):
    return f"={n}"
"""

_BETA_TEST = """
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from beta.src.fmt import fmt


def test_fmt():
    assert fmt(3) == "=3"
"""

_INTEGRATION_ONLY_BETA = """
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from beta.src.fmt import fmt


def test_pipeline():
    # ★ 只用到 beta —— alpha 完全没有参与
    assert fmt(5) == "=5"
"""

_INTEGRATION_BOTH = """
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from alpha.src.calc import add
from beta.src.fmt import fmt


def test_pipeline():
    assert fmt(add(2, 3)) == "=5"
"""


def _workspace(tmp_path: Path, integration_source: str) -> Path:
    ws = tmp_path / "ws"
    for module, src_name, src, test in (
        ("alpha", "calc.py", _ALPHA_SRC, _ALPHA_TEST),
        ("beta", "fmt.py", _BETA_SRC, _BETA_TEST),
    ):
        (ws / module / "src").mkdir(parents=True)
        (ws / module / "tests").mkdir(parents=True)
        (ws / module / "src" / src_name).write_text(src, encoding="utf-8")
        (ws / module / "tests" / f"test_{module}.py").write_text(test, encoding="utf-8")

    (ws / "integration").mkdir(parents=True)
    (ws / "integration" / "test_pipeline.py").write_text(integration_source, encoding="utf-8")
    return ws


_COMMAND = [sys.executable, "-m", "pytest", "integration", "-q"]
_MODULES = ["alpha", "beta"]


# ══════════════════════════════════════════════════════════════
#  两个方向都要钉住
# ══════════════════════════════════════════════════════════════


def test_module_absent_from_integration_is_reported(tmp_path: Path) -> None:
    """★ 假集成必须被拦下：集成测试只用了 beta，alpha 却也被宣称集成过。

    这是「各段都对、合起来不通」的可执行形态 ——
    alpha 自己的测试全绿、beta 自己的测试全绿、集成测试也全绿，
    但集成测试**从来没有碰过 alpha**。
    """

    ws = _workspace(tmp_path, _INTEGRATION_ONLY_BETA)
    report = composition_check(ws, _COMMAND, modules=_MODULES).uncovered

    assert report is not None, "集成测试没碰过 alpha，却判为通过"
    assert "alpha" in report
    assert "beta" not in report, "beta 确实被集成测试用到了，不该报它"


def test_real_integration_passes(tmp_path: Path) -> None:
    """★ 真集成必须放行 —— 否则这个算子只是个永远拦人的门。

    只有假集成会被拦、真集成能过，它才有区分能力。
    """

    ws = _workspace(tmp_path, _INTEGRATION_BOTH)
    assert composition_check(ws, _COMMAND, modules=_MODULES).uncovered is None


def test_module_own_tests_do_not_count_as_integration_coverage(tmp_path: Path) -> None:
    """★ 这条守的是这个算子**自己不是空的**。

    alpha 有一份**会因为桩化而变红**的自测（test_alpha.py）。
    如果检查时不把它藏起来，桩化 alpha 一定会让整份套件变红 ——
    于是 alpha 被判为「已覆盖」，而实际上集成测试根本没碰过它。

    换句话说：不藏自测的话，**这个检查会永远返回「全部覆盖」**，
    看起来一切正常，实际什么都没检。

    这里用一条把 alpha 自测也纳入作用域的谓词来验证那一步确实生效。
    """

    ws = _workspace(tmp_path, _INTEGRATION_ONLY_BETA)
    # ★ 谓词跑**整个工作区**，包含 alpha 自己的测试
    whole_workspace = [sys.executable, "-m", "pytest", ".", "-q"]

    report = composition_check(ws, whole_workspace, modules=_MODULES).uncovered
    assert report is not None and "alpha" in report, (
        "自测没被藏起来 —— alpha 靠自己的单元测试变红冒充了集成覆盖"
    )


# ══════════════════════════════════════════════════════════════
#  桩的性质与还原
# ══════════════════════════════════════════════════════════════


def test_stub_is_importable_not_a_missing_file(tmp_path: Path) -> None:
    """★ 桩必须**签名保真**：删文件得到的 ImportError 是「红对了、理由错了」。

    ImportError 只证明「A 的文件存在」，证明不了「集成测试覆盖了 A 的行为」——
    一个只写了 `import a` 的集成测试在删文件时也会红。
    """

    from codentum_engine.acceptance import _stub_source  # noqa: PLC2701

    stubbed = _stub_source(
        "import os\n\nMAX = 10\n\n\nclass C:\n    def m(self, x: int) -> int:\n        return x * 2\n"
    )
    assert stubbed is not None
    namespace: dict[str, object] = {}
    exec(compile(stubbed, "<stub>", "exec"), namespace)  # noqa: S102

    assert namespace["MAX"] == 10, "模块级常量必须保留 —— 否则导入它的地方会 ImportError"
    instance = namespace["C"]()  # type: ignore[operator]
    assert instance.m(3) is None, "函数体必须被掏空"


def test_workspace_is_restored_byte_for_byte(tmp_path: Path) -> None:
    """★ 还原失败会把工作区留在残破状态，而那比判错严重得多。"""

    ws = _workspace(tmp_path, _INTEGRATION_ONLY_BETA)
    before = {p: p.read_bytes() for p in sorted(ws.rglob("*.py"))}

    composition_check(ws, _COMMAND, modules=_MODULES)

    after = {p: p.read_bytes() for p in sorted(ws.rglob("*.py"))}
    assert after == before, "工作区没有被逐字还原"
    assert not list(ws.rglob("*.composition-check")), "藏起来的自测没有复原"


# ══════════════════════════════════════════════════════════════
#  模块发现与门禁接线
#
#  ★ 造好了不接上，就是这个仓库一路在修的那个病
#    （langgraph 装了零 import；mcp_config_dir 无人传）。
#    所以接线本身也要有判据。
# ══════════════════════════════════════════════════════════════


def test_discover_modules_follows_planner_layout(tmp_path: Path) -> None:
    """★ 从**工作区的实际状态**推导模块，而不是从 packet 上的声明读。

    声明会与现实漂移（改了目录忘了改声明），目录不会 ——
    而这一层要判的正是「实际存在的模块有没有被集成测试覆盖」。
    """

    from codentum_engine.acceptance import discover_modules

    ws = _workspace(tmp_path, _INTEGRATION_ONLY_BETA)
    assert discover_modules(ws) == ["alpha", "beta"]


def _integration_packet(pid: str, predicate: str, role: str):  # type: ignore[no-untyped-def]
    from codentum_contracts.state import (
        Acceptance,
        BudgetGrant,
        EvidenceRef,
        ModelRouting,
        PacketId,
        Provenance,
        WorkPacket,
    )

    return WorkPacket(
        id=PacketId(pid),
        kind="integrate",
        state="review",
        role=role,  # type: ignore[arg-type]
        ownsPaths=("workspace/",),
        readsPaths=(),
        deps=(),
        acceptance=Acceptance(
            kind="test", predicate=predicate, threshold=None, authoredBy="reviewer"
        ),
        budget=BudgetGrant(
            currency="CNY", limitCny=1.0, spentCny=0.0, degradationChain=("drop_semantic",)
        ),
        routing=ModelRouting(model="m", effort="medium", batch=None),
        attempts=1,
        evidence=(EvidenceRef("file:model/result.json"),),
        provenance=Provenance(createdBy="intake", createdAt="2026-08-14T00:00:00Z", parent=None),
    )


def _gate_workspace(tmp_path: Path, pid: str) -> tuple[Path, Path]:
    """把双模块工作区铺到门禁会去找的位置：<workers_root>/<pid>/attempt-1/。"""

    workers_root = tmp_path / "codentum-workers"
    attempt = workers_root / pid / "attempt-1"
    attempt.mkdir(parents=True)

    built = _workspace(tmp_path, _INTEGRATION_ONLY_BETA)
    for path in sorted(built.rglob("*")):
        target = attempt / path.relative_to(built)
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.write_bytes(path.read_bytes())
    return workers_root, attempt


def test_gate_rejects_integration_that_skips_a_module(tmp_path: Path) -> None:
    """★ 端到端：集成 packet 的验收谓词自己是绿的，门禁仍然拦下它。

    这正是「各段都对、合起来不通」被判出来的那一刻 ——
    谓词退出码 0、证据齐全、验收作者也不是自己，
    唯一的问题是**集成测试从来没碰过 alpha**。
    """

    from codentum_engine.acceptance import build_executing_acceptance_gate

    pid = "wp-integ00001"
    workers_root, _ = _gate_workspace(tmp_path, pid)
    gate = build_executing_acceptance_gate(workers_root)

    verdict = gate(
        _integration_packet(pid, f"{sys.executable} -m pytest integration -q", "integrator")
    )
    assert not verdict.passed, "集成测试没碰过 alpha，门禁却放行了"
    # ★ 必须断言**第六层特有的措辞**，不能只断言 "alpha" 出现过。
    #
    #   第五层 vacuity_check 的失败消息里会列出测试文件路径
    #   （alpha/tests/test_alpha.py）—— 它同样包含 "alpha"。
    #   只断言 "alpha" 的话，这条测试可能是被第五层拦下而通过的，
    #   **根本没走到第六层**，而我们看不出区别。
    assert "集成测试没有覆盖" in verdict.detail, f"不是第六层拦的：{verdict.detail[:200]}"
    assert "alpha" in verdict.detail


def test_gate_skips_composition_for_non_integration_packets(tmp_path: Path) -> None:
    """★ 单模块 packet 谈「组合」没有意义 —— 那一层由 vacuity_check 守。

    不加这条限制的话，每个普通 coder packet 都要付一遍逐模块桩化的代价，
    而且会因为「它没集成别的模块」被误拦。
    """

    from codentum_engine.acceptance import build_executing_acceptance_gate

    pid = "wp-integ00002"
    workers_root, _ = _gate_workspace(tmp_path, pid)
    gate = build_executing_acceptance_gate(workers_root)

    verdict = gate(
        _integration_packet(pid, f"{sys.executable} -m pytest integration -q", "coder")
    )
    assert "集成测试没有覆盖" not in verdict.detail, "非集成 packet 不该跑组合检验"


def test_inconclusive_is_distinguishable_from_covered(tmp_path: Path) -> None:
    """★ 「全部覆盖」与「根本没检成」不能返回同一个值。

    最初这个函数只返回 `str | None`，两者都是 None ——
    门禁随后写的是「验收通过」，**而那正是谎称检过了**。

    没检成不拦人是对的（检不了就别拦），但它必须**说出来**：
    否则负载高的环境里这一层会静默失效，而报告上看不出区别。

    ★ 这个缺陷是真机撞出来的：一次并发跑测试时子进程超时，
      composition_check 返回 None，门禁照常写「验收通过」。
    """

    ws = _workspace(tmp_path, _INTEGRATION_ONLY_BETA)
    result = composition_check(ws, ["definitely-not-a-real-command-xyz"], modules=_MODULES)

    assert result.uncovered is None, "没检成不该拦人"
    assert result.inconclusive is not None, "没检成却和「全部覆盖」返回了同一个值"
    assert "没有检过" in result.inconclusive
