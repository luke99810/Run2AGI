"""执行过程必须在磁盘上可见 —— 否则桌面端在整段执行期间显示的是假象。

════════════════════════════════════════════════════════════════
 这条测试是怎么来的
════════════════════════════════════════════════════════════════

2026-08-11 引擎第一次真机跑通时实测到：

    09:57:07  提交需求，packet 落盘为 pending
    09:57:07 ~ 10:01:16   ★ 磁盘状态零变化，一直是 pending
    10:01:16  模型返回，四次状态变更**一起**出现，直接跳到 accepted

原因是 `ReconcileLoop.run_until_stable()` 内部连跑多轮，**只在最外层
保存一次**。那个方法本身没错 —— 它是给测试用的（load → 跑完 → 断言终态）。
错的是产品入口照抄了它。

后果：一个卖点是「执行过程看得见」的产品，恰好在执行过程中什么都看不见。
而且这种缺陷不会报错、不会变红、日志里一切正常 ——
桌面端只是显示「等待中」，然后突然显示「完成」。

★ 判据不能是「我记得每轮都存了」，得是「不存会有东西变红」。
"""

from __future__ import annotations

import json
import subprocess
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
    dump_state,
)
from codentum_engine.service import EngineConfig, EngineService

_KEY_ENVS = ("DASHSCOPE_API_KEY", "BAILIAN_API_KEY", "QWEN_API_KEY", "AGENTTEAMS_LLM_API_KEY")


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    for name in _KEY_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "REPLACE_ME_DASHSCOPE_API_KEY_FOR_TESTS")
    return root


def _packet(
    pid: str, state: str, evidence: tuple[EvidenceRef, ...] = ()
) -> WorkPacket:
    return WorkPacket(
        id=PacketId(pid),
        kind="impl",
        state=state,  # type: ignore[arg-type]
        role="coder",
        ownsPaths=("workspace/",),
        readsPaths=(),
        deps=(),
        acceptance=Acceptance(
            kind= "manual",
            predicate= "operator-review: 占位",
            threshold= None,
            authoredBy= "qa",
        ),
        budget=BudgetGrant(
            currency= "CNY",
            limitCny= 1.0,
            spentCny= 0.0,
            degradationChain= ("drop_semantic",),
        ),
        routing=ModelRouting(model= "qwen-coder-plus-1106", effort= "medium", batch= None),
        attempts=1,
        evidence=evidence,
        provenance=Provenance(
            createdBy= "intake",
            createdAt= "2026-08-11T00:00:00Z",
            parent= None,
        ),
    )


def _seed(project: Path, *packets: WorkPacket) -> Path:
    state_dir = project / ".codentum"
    (state_dir / "packets").mkdir(parents=True, exist_ok=True)
    for packet in packets:
        (state_dir / "packets" / f"{packet.id}.json").write_text(
            json.dumps(dump_state(packet), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    return state_dir


def test_the_background_run_flushes_once_per_tick(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 判据是**落盘次数**，不是「磁盘上出现过几个状态」。

    第一版写成「磁盘上必须看到 ≥2 个不同状态」，看起来合理，实际上是空转的：
    起点 pending、终点 ready 本来就是两个状态，用 `run_until_stable`
    （只在最后保存一次）照样能看到两个。**换回退化写法，那一版仍然全绿。**

    —— 这正是本项目记过的那条：对照组配错了，实验照样出数。

    所以改成直接数 `save_state` 被调了几次，并跑**真正的**
    `_run_until_stable`，不是测试里手搓的循环（手搓循环只能证明我会写循环，
    证明不了产品代码是那么写的）。
    """

    _seed(project, _packet("wp-flush0001", "pending"))

    from codentum_control_plane.reconcile import ReconcileLoop

    saves: list[int] = []
    original = ReconcileLoop.save_state

    def counting_save(self: ReconcileLoop) -> None:
        saves.append(1)
        original(self)

    monkeypatch.setattr(ReconcileLoop, "save_state", counting_save)

    service = EngineService(EngineConfig(project_root=project))
    service._run_until_stable(max_ticks=6)  # noqa: SLF001

    assert len(saves) >= 2, (
        f"整个后台执行只落盘了 {len(saves)} 次 —— 说明用的是"
        f"「跑完再存」而不是「每轮都存」。一次真实模型调用要 30～240 秒，"
        f"这段时间里桌面端读到的会一直是起始状态。"
    )


def test_progress_is_visible_on_disk_while_the_run_is_still_going(project: Path) -> None:
    """★ 上一条数的是次数，这一条看的是内容：落盘的必须是**当轮的真实状态**。

    次数对但每次都写同一份旧内容，桌面端看到的仍然是假象。
    """

    state_dir = _seed(project, _packet("wp-flush0002", "pending"))
    service = EngineService(EngineConfig(project_root=project))
    loop = service._build_loop()  # noqa: SLF001
    loop.load_state()

    loop.tick()
    loop.save_state()

    in_memory = next(iter(loop.packets.values())).state
    on_disk = json.loads(
        (state_dir / "packets" / "wp-flush0002.json").read_text("utf-8")
    )["state"]
    assert on_disk == in_memory, (
        f"内存里已经是 {in_memory}，磁盘上还是 {on_disk} —— 桌面端读的是磁盘"
    )
    assert in_memory != "pending", "第一轮就该推进，否则这条测试是空转的"


def test_the_service_does_not_call_run_until_stable(project: Path) -> None:
    """★ 直接盯住那个具体的退化方式。

    `run_until_stable` 只在最外层保存一次。它是给测试用的，不是给产品入口
    用的。这条测试比上面那条更脆，但它给出的是**修法**而不只是症状 ——
    上面那条红了以后，人会来这里看该改哪。
    """

    source = (
        Path(__file__).resolve().parents[1] / "codentum_engine" / "service.py"
    ).read_text("utf-8")
    call_sites = [
        line.strip()
        for line in source.splitlines()
        if "run_until_stable" in line and line.strip().startswith(("loop.", "self.", "return"))
    ]
    assert not call_sites, (
        "service.py 直接调了 loop.run_until_stable —— 它内部只保存一次，"
        f"执行过程会对桌面端不可见：{call_sites}"
    )
