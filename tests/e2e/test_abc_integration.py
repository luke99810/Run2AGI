"""A + B + C 三方集成：控制平面 → 真实 Harness → 桌面端可读的 .codentum/

为什么需要这一份
----------------
三段各自都有测试，但**接缝没有**：

1. `test_reconcile.py::test_pending_to_accepted_with_worker` 跑的是全链路，
   但用 `_MockWorkerRuntime` —— **B 的真 runtime 从没和 A 的 loop 一起跑过**。
   08-08 提交信息里的「A+B 闭环贯通」是手工验证，不是可重复的测试。
2. `LocalWorkerRuntime` 只在 `packages/harness/tests/` 内部被测。
3. `save_state()` 的往返只在 `TestPersistence` 单独测，**没和闭环连起来** ——
   「跑完一个 packet 之后落盘长什么样」是个空档。
4. C 读 `.codentum/`，但它的运行时校验是**手写守卫**
   （`directory-state-source.ts::isGraphFile`），不是从 schema 生成的。
   A 按 Pydantic 写、C 按手写守卫读，**两边可能分叉且不报错**。

装配须知（写出来是因为这两条都没写在任何接口签名上）
--------------------------------------------------
- `LocalWorkerRuntimeConfig.repo_root` 必须是**被开发的项目仓库**，而且**必须是 git 仓库**
  （`worker/worktree.py` 会跑 `git rev-parse`）。
- A 的 `_build_spawn_request` 把 workspace 硬编码为
  `<项目父目录>/codentum-workers/<pid>/attempt-N`，而 B 要求 workspace
  **在 repo_root 之外**。所以 `repo_root` 只能是项目目录本身，
  填成 `codentum-workers` 会永远失败 —— 且失败被吞掉（见 KNOWN-1）。
- ★ **`LocalWorkerRuntimeConfig.runner` 默认是 `None`**，而 `None` 意味着
  「没有执行器」：worker 会建好 worktree、写好 manifest 和 prompt bundle，
  然后以 `runtime_error: no worker runner configured` 收场。
  不配 runner，**任何 packet 都不可能被真实执行**。
  这一条曾被误判成「AgentTeams 没装」—— 其实与 AgentTeams 无关，
  是一行配置。见 `TestRealExecution`。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from codentum_contracts.state import PacketId, WorkPacket, dump_state
from codentum_control_plane.budget import BudgetTracker
from codentum_control_plane.reconcile import ReconcileLoop
from codentum_harness.runtime import (
    LocalWorkerRuntimeConfig,
    RunnerConfig,
    build_local_worker_runtime,
)


# ════════════════════════════════════════════════════════════════
#  Helper
# ════════════════════════════════════════════════════════════════

def _make_packet(
    pid: str,
    state: str = "pending",
    deps: tuple[str, ...] = (),
    owns: tuple[str, ...] = ("src/abc/",),
    role: str = "coder",
) -> WorkPacket:
    return WorkPacket(
        id=PacketId(pid),
        kind="impl",
        state=state,  # type: ignore[arg-type]
        role=role,  # type: ignore[arg-type]
        ownsPaths=owns,
        readsPaths=("tests/",),
        deps=tuple(PacketId(d) for d in deps),
        acceptance={"kind": "test", "predicate": "pytest", "authoredBy": "qa"},
        budget={
            "currency": "CNY",
            "limitCny": 5.0,
            "spentCny": 0.0,
            "degradationChain": ("drop_semantic",),
        },
        attempts=0,
        evidence=(),
        provenance={"createdBy": "planner", "createdAt": "2026-08-05T00:00:00Z"},
    )


def _write_packet(state_dir: Path, packet: WorkPacket) -> None:
    """把 packet 落盘到 `.codentum/packets/`。

    刻意**不**直接塞 `loop._packets` —— 生产路径是 `load_state()` 从磁盘读，
    绕过它就测不到「磁盘是不是唯一真源」。
    """
    pf = state_dir / "packets" / f"{packet.id}.json"
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(
        json.dumps(dump_state(packet), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _git_init(path: Path) -> None:
    """把目录初始化成可派生 worktree 的 git 仓库。"""
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "e2e@codentum.local"],
        ["git", "config", "user.name", "codentum-e2e"],
    ):
        subprocess.run(cmd, cwd=path, check=True, capture_output=True)
    (path / ".gitkeep").write_text("", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "e2e base"], cwd=path,
                   check=True, capture_output=True)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """被开发的项目仓库；`.codentum/` 在它下面，worker 工作区是它的兄弟目录。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    return repo


def _make_loop(project: Path) -> ReconcileLoop:
    loop = ReconcileLoop(state_dir=str(project / ".codentum"))
    loop.load_state()
    loop.worker_runtime = build_local_worker_runtime(
        LocalWorkerRuntimeConfig(repo_root=project)
    )
    return loop


# ════════════════════════════════════════════════════════════════
#  Tests: A + B（真实 runtime）
# ════════════════════════════════════════════════════════════════

class TestABWithRealRuntime:
    def test_real_runtime_builds(self, project: Path) -> None:
        """B 的 runtime 在 A 的调用姿势下构造得起来。

        单独一条，是为了让「构造失败」与「跑不通」在报错时能区分开。
        """
        rt = build_local_worker_runtime(LocalWorkerRuntimeConfig(repo_root=project))
        assert rt is not None
        assert hasattr(rt, "spawn"), "WorkerRuntime 契约要求 spawn 是唯一入口"

    def test_full_chain_runs_and_settles_honestly(self, project: Path) -> None:
        """pending → ready → running → review，用 B 的真 runtime，且终态与 worker 结果一致。

        ★ 不要断言「一定到 accepted」。真 runtime 在没装 AgentTeams 的机器上会以
          runtime_error 收场，那时正确的终态就是 review —— 硬写 accepted 的话，
          这条测试只会在验收门禁漏放行的时候变绿，坏掉的时候反而绿得最稳。
          该断言的是：链路确实跑完了，且终态**如实反映**了 worker 的结果。
        """
        state_dir = project / ".codentum"
        _write_packet(state_dir, _make_packet("wp-abc001", owns=("src/abc001/",)))

        loop = _make_loop(project)
        report = loop.run_until_stable(max_ticks=30)
        final = loop.packet(PacketId("wp-abc001"))
        trace = [(t.from_state, t.to_state, t.detail) for t in report.transitions]

        # 链路必须真的走过调度与执行，而不是一动没动
        seen = {t.to_state for t in report.transitions}
        assert {"ready", "running"} <= seen, f"链路没跑起来。轨迹: {trace}"
        assert final.state in ("accepted", "review"), f"落在意外状态 {final.state}。轨迹: {trace}"

        worker_failed = any("Worker 失败" in (t.detail or "") for t in report.transitions)
        if worker_failed:
            assert final.state == "review", f"worker 失败却走到 {final.state}。轨迹: {trace}"
        else:
            assert final.state == "accepted", f"worker 成功却停在 {final.state}。轨迹: {trace}"

    def test_parallel_packets_no_lock_conflict(self, project: Path) -> None:
        """三个互不重叠路径的 packet 应能并行推进，且各自拿到锁（I1 不被误伤）。

        ★ 这条测的是**锁不互相误伤**，不是验收结果。所以断言落在
          「每个 packet 都真的被调度进 running」，而不是「都 accepted」——
          后者取决于 worker 跑没跑成，与 I1 无关。
        """
        state_dir = project / ".codentum"
        for i in range(1, 4):
            _write_packet(state_dir, _make_packet(f"wp-abc10{i}", owns=(f"src/abc10{i}/",)))

        loop = _make_loop(project)
        report = loop.run_until_stable(max_ticks=60)

        states = {str(k): v.state for k, v in loop.packets.items()}
        ran = {
            str(t.packet_id) for t in report.transitions if t.to_state == "running"
        }
        assert ran == set(states), (
            f"路径互不重叠却没能全部拿到锁：进入过 running 的只有 {sorted(ran)}，"
            f"全部 packet 为 {sorted(states)}"
        )
        # 谁也不该被卡在 blocked —— 那才是锁误伤的症状
        assert not [k for k, s in states.items() if s == "blocked"], (
            f"有 packet 被阻塞，疑似锁误伤: {states}"
        )


# ════════════════════════════════════════════════════════════════
#  Tests: A → C（磁盘交接）
# ════════════════════════════════════════════════════════════════

def assert_matches_desktop_guard(state_dir: Path) -> dict:
    """断言 `graph.json` 能过 C 的 `isGraphFile` 守卫。

    条件逐条抄自 `directory-state-source.ts::isGraphFile`（916 行起）——
    因为 C 的运行时校验是手写的，不是从 schema 生成的，
    所以这里必须照着**它**写，而不是照着 schema 写。
    """
    graph_path = state_dir / "graph.json"
    assert graph_path.exists(), f"C 要求 graph.json 必须存在: {graph_path}"
    g = json.loads(graph_path.read_text(encoding="utf-8"))

    assert isinstance(g, dict), "顶层必须是对象"
    assert g.get("schemaVersion") == 1, "isGraphFile 硬要求 schemaVersion === 1"

    dep, own = g.get("dependency"), g.get("ownership")
    assert isinstance(dep, dict) and isinstance(own, dict)
    assert isinstance(dep.get("nodes"), list) and all(
        isinstance(n, str) for n in dep["nodes"]
    ), "dependency.nodes 必须是 string[]"
    assert isinstance(dep.get("edges"), list) and all(
        isinstance(e, dict) and isinstance(e.get("from"), str) and isinstance(e.get("to"), str)
        for e in dep["edges"]
    ), "dependency.edges 每项必须是 {from:string,to:string}"
    assert isinstance(own.get("locks"), list) and all(
        isinstance(lk, dict)
        and isinstance(lk.get("pathPrefix"), str)
        and isinstance(lk.get("heldBy"), str)
        and isinstance(lk.get("acquiredAt"), str)
        for lk in own["locks"]
    ), "ownership.locks 每项三个字段都必须是 string"
    assert isinstance(own.get("version"), int), "ownership.version 必须是整数"
    return g


class TestACViaDisk:
    def test_graph_json_passes_desktop_guard(self, project: Path) -> None:
        """A 落盘的 graph.json 结构上能过 C 的守卫。"""
        state_dir = project / ".codentum"
        _write_packet(state_dir, _make_packet("wp-abc002", owns=("src/abc002/",)))

        loop = _make_loop(project)
        loop.run_until_stable(max_ticks=30)
        loop.save_state()

        assert_matches_desktop_guard(state_dir)

    def test_reload_preserves_states(self, project: Path) -> None:
        """save → 新 loop load → 状态一致。

        C 是**旁观者**，读的是磁盘不是内存，所以「磁盘能否独立还原内存」
        直接决定桌面端显示的对不对。
        """
        state_dir = project / ".codentum"
        _write_packet(state_dir, _make_packet("wp-abc201", owns=("src/abc201/",)))

        loop = _make_loop(project)
        loop.run_until_stable(max_ticks=30)
        loop.save_state()
        before = {str(k): v.state for k, v in loop.packets.items()}

        reloaded = ReconcileLoop(state_dir=str(state_dir))
        reloaded.load_state()
        after = {str(k): v.state for k, v in reloaded.packets.items()}

        assert before == after, f"落盘往返后状态漂移：{before} != {after}"


# ════════════════════════════════════════════════════════════════
#  A/B/C 接缝的回归防线
#
#  这两条曾经是 xfail(strict) 记录的真实缺陷，现已修复。
#  它们的共同点是**静默**：坏掉的时候没有异常、没有红灯，
#  只是桌面端安静地显示不一致、失败的活安静地变成验收通过。
#  所以这两条必须一直留着。
# ════════════════════════════════════════════════════════════════

class TestIntegrationInvariants:
    def test_graph_nodes_match_packets(self, project: Path) -> None:
        """graph.dependency.nodes 应与 packets/ 一致，否则 C 判定数据不连贯。"""
        state_dir = project / ".codentum"
        for i in (1, 2):
            _write_packet(state_dir, _make_packet(f"wp-abc30{i}", owns=(f"src/abc30{i}/",)))

        loop = _make_loop(project)
        loop.run_until_stable(max_ticks=40)
        loop.save_state()

        g = json.loads((state_dir / "graph.json").read_text(encoding="utf-8"))
        nodes = set(g["dependency"]["nodes"])
        packet_ids = {p.stem for p in (state_dir / "packets").glob("*.json")}
        assert nodes == packet_ids, (
            f"graph.dependency.nodes={sorted(nodes)} 与 packets/={sorted(packet_ids)} 不一致"
        )

    def test_failed_worker_must_not_be_accepted(self, project: Path) -> None:
        """worker 失败的 packet 不该进 accepted。

        ★ 这里跑的是**真的 B**（LocalWorkerRuntime），不是 mock。
          在没装 AgentTeams 的机器上 worker 会以 runtime_error 收场，
          正好构成这条不变量的天然用例。
        """
        state_dir = project / ".codentum"
        _write_packet(state_dir, _make_packet("wp-abc401", owns=("src/abc401/",)))

        loop = _make_loop(project)
        report = loop.run_until_stable(max_ticks=30)

        worker_failed = any(
            "Worker 失败" in (t.detail or "") for t in report.transitions
        )
        final = loop.packet(PacketId("wp-abc401"))
        if worker_failed:
            assert final.state != "accepted", (
                "worker 失败却被 accepted。轨迹：\n  "
                + "\n  ".join(f"{t.from_state} -> {t.to_state} {t.detail}"
                              for t in report.transitions)
            )
            assert any(
                ref.startswith("sys:worker-failed:") for ref in final.evidence
            ), f"失败没有落成机器可读的证据：{final.evidence}"


# ════════════════════════════════════════════════════════════════
#  P0 的真判据：worker 真的改了代码，并因此被验收
# ════════════════════════════════════════════════════════════════

#: 冒充 coder 的最小 worker —— 在 workspace 里真的写一个源文件。
#: 用真实文件改动当判据，是因为「状态变成 accepted」本身证明不了任何事：
#: 那正是之前假绿灯骗过所有人的地方。
#: 用 chr(10) 拼换行，不写 "\\n" —— 这段字符串要经过
#: 「本文件的 Python 字面量 → 命令行参数 → 子进程的 Python 字面量」三层，
#: 反斜杠在哪一层被吃掉都只表现为「退出码 1」，排查成本远高于写得笨一点。
_NL = "' + chr(10) + '"
_FAKE_CODER = (
    "import pathlib;"
    "p = pathlib.Path('src/app/feature.py');"
    "p.parent.mkdir(parents=True, exist_ok=True);"
    f"p.write_text('def feature():{_NL}    return 42{_NL}', encoding='utf-8');"
    "print('wrote', p)"
)


class TestRealExecution:
    """P0 判据：一个 packet 被认领 → 真实执行 → 验收，且改动在 Git 可见。

    ★ 与 TestABWithRealRuntime 的区别是**配了 runner**。
      `LocalWorkerRuntimeConfig.runner` 默认 None，那时 worker 一定失败。
      不配 runner 就永远走不到这里 —— 这一条曾被误判成环境问题。
    """

    def test_worker_really_changes_code_and_gets_accepted(self, project: Path) -> None:
        (project / "src" / "app").mkdir(parents=True, exist_ok=True)
        (project / "src" / "app" / "main.py").write_text(
            "def main():\n    pass\n", encoding="utf-8"
        )
        subprocess.run(["git", "add", "-A"], cwd=project, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "base app"], cwd=project,
                       check=True, capture_output=True)

        state_dir = project / ".codentum"
        _write_packet(state_dir, _make_packet("wp-p00001", owns=("src/app/",)))

        loop = ReconcileLoop(
            state_dir=str(state_dir),
            budget_tracker=BudgetTracker(limit_cny=20.0),
        )
        loop.load_state()
        loop.worker_runtime = build_local_worker_runtime(
            LocalWorkerRuntimeConfig(
                repo_root=project,
                runner=RunnerConfig.command_runner(
                    [sys.executable, "-c", _FAKE_CODER], timeout_seconds=60.0
                ),
            )
        )

        report = loop.run_until_stable(max_ticks=30)
        loop.save_state()
        trace = "\n  ".join(
            f"{t.from_state} -> {t.to_state} | {t.detail}" for t in report.transitions
        )
        final = loop.packet(PacketId("wp-p00001"))

        # 1) worker 真的改了文件 —— 这是 P0 的实质，不是状态字段
        workspace = project.parent / "codentum-workers" / "wp-p00001" / "attempt-1"
        feature = workspace / "src" / "app" / "feature.py"
        assert feature.exists(), f"worker 没有产生任何代码改动。轨迹：\n  {trace}"
        assert "return 42" in feature.read_text(encoding="utf-8")

        # 2) 改动在 Git 里看得见（赛题判据原文：状态变更 Git 可见）
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=workspace, capture_output=True
        ).stdout.decode("utf-8", "replace")
        assert "src/app/feature.py" in status, f"改动不在 git 视野里：{status!r}"

        # 3) 验收依据必须是真实证据，不能是控制面自己的簿记
        assert final.state == "accepted", f"未被验收。轨迹：\n  {trace}"
        real = [e for e in final.evidence if not e.startswith("sys:")]
        assert real, f"验收了，但没有一条真实证据：{list(final.evidence)}"

    def test_missing_runner_fails_loudly(self, project: Path) -> None:
        """不配 runner 时，失败原因必须说得出是「没配 runner」。

        ★ 回归防线：控制面原来只保留 reason_code（runtime_error），
          把 outcome.detail 丢了。而 runtime_error 只有 8 个取值之一，
          真因「no worker runner configured」完全看不出来 ——
          排查方向会被带向环境问题。
        """
        state_dir = project / ".codentum"
        _write_packet(state_dir, _make_packet("wp-norun1", owns=("src/norun/",)))

        loop = _make_loop(project)  # 这个 helper 不配 runner
        report = loop.run_until_stable(max_ticks=30)

        details = " | ".join(t.detail or "" for t in report.transitions)
        assert "runner" in details, (
            f"失败原因没有指向 runner 配置，排查会被带偏：{details}"
        )
