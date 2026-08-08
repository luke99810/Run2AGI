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
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from codentum_contracts.state import PacketId, WorkPacket, dump_state
from codentum_control_plane.reconcile import ReconcileLoop
from codentum_harness.runtime import (
    LocalWorkerRuntimeConfig,
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

    def test_full_chain_reaches_terminal_state(self, project: Path) -> None:
        """pending → ready → running → review → accepted，用 B 的真 runtime。

        ★ 这是 08-08 那次手工验证的可重复版本。
        """
        state_dir = project / ".codentum"
        _write_packet(state_dir, _make_packet("wp-abc001", owns=("src/abc001/",)))

        loop = _make_loop(project)
        report = loop.run_until_stable(max_ticks=30)
        final = loop.packet(PacketId("wp-abc001")).state

        assert final == "accepted", (
            f"真 runtime 下未到 accepted，实际 {final}。\n"
            f"轨迹: {[(t.from_state, t.to_state, t.detail) for t in report.transitions]}"
        )

    def test_parallel_packets_no_lock_conflict(self, project: Path) -> None:
        """三个互不重叠路径的 packet 应能并行推进到终态（I1 不被误伤）。"""
        state_dir = project / ".codentum"
        for i in range(1, 4):
            _write_packet(state_dir, _make_packet(f"wp-abc10{i}", owns=(f"src/abc10{i}/",)))

        loop = _make_loop(project)
        loop.run_until_stable(max_ticks=60)

        states = {str(k): v.state for k, v in loop.packets.items()}
        assert all(s == "accepted" for s in states.values()), f"未全部到终态: {states}"


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
#  已知缺陷（xfail strict：修好了会变红，提醒删掉标记）
# ════════════════════════════════════════════════════════════════

class TestKnownDefects:
    @pytest.mark.xfail(
        strict=True,
        reason=(
            "KNOWN-2：save_state() 写的 dependency.nodes 恒为空 —— _dep_graph 只从磁盘读"
            "（loop.py:108 起），从不由 packets 推导。C 的 checkGraphPacketCoherence"
            "（directory-state-source.ts:873）会因此把每一次全新流程都判成 "
            "[partial-write] 并置 coherent=false。桌面端不会崩，只会安静地显示不一致。"
        ),
    )
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

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "KNOWN-3：worker 失败仍被 accepted。_try_review_to_accepted 在 "
            "gate_runner=None 时走兜底分支（loop.py:569），条件只是『evidence 非空』；"
            "而 packet 手里那条证据是 _try_ready_to_running 自己生成的 sys:lock:<pid>。"
            "于是『系统记了一笔自己获取锁』被当成了验收依据 —— I6 要求的是证据，"
            "拿到的却是记账。ReconcileLoop 的 gate_runner/guardian/budget_tracker/"
            "transition_table 四个安全组件全部默认 None，默认配置下护栏是全关的。"
        ),
    )
    def test_failed_worker_must_not_be_accepted(self, project: Path) -> None:
        """worker 失败的 packet 不该进 accepted。"""
        state_dir = project / ".codentum"
        _write_packet(state_dir, _make_packet("wp-abc401", owns=("src/abc401/",)))

        loop = _make_loop(project)
        report = loop.run_until_stable(max_ticks=30)

        worker_failed = any(
            "Worker 失败" in (t.detail or "") for t in report.transitions
        )
        final = loop.packet(PacketId("wp-abc401")).state
        if worker_failed:
            assert final != "accepted", (
                "worker 失败却被 accepted。轨迹：\n  "
                + "\n  ".join(f"{t.from_state} -> {t.to_state} {t.detail}"
                              for t in report.transitions)
            )
