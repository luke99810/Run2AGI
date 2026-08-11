"""把「打开 TransitionTable 会让 coder packet 永远停在 review」这件事钉住。

════════════════════════════════════════════════════════════════
 为什么这是一条测试，而不是一句注释
════════════════════════════════════════════════════════════════

`EngineConfig.enforce_role_transitions` 默认 False。在一个刚做完护栏消融
实验、结论是「四个安全组件默认全 None 很危险」的项目里，入口层再默认关掉
一个安全组件，需要的不是一句解释，是一条会变红的证据。

事实是这样的：

  - `reviewer` 的 RoleSpec 声明了 `review → accepted`（requiresGate: review）
  - `coder` 没有声明 —— 它只有 `running → review` 和 `running → blocked`
  - `_try_review_to_accepted` 用 **`role=packet.role`** 去查表

于是对一个 coder 的 packet，表的回答是「不允许」。表本身是对的 ——
「coder 不能给自己的活签字」正是要的语义 —— 但 reconcile 提问的方式
（拿 packet 自己的 role 去问「谁能验收它」）让这条规则等价于
**「没有任何人能验收 coder 的 packet」**。

这是 A 控制平面里的一处建模问题，不是配置问题。正确的修法是让验收侧的查询
用「验收者的角色」而不是「packet 的角色」，或者把验收明确建模成另一个
reviewer packet。两条路都是契约级 / 控制平面级的改动，不该在接线的时候
顺手做掉。

★ 所以这里用一条测试把**两个分支**都描述出来：
  修好之后，`test_enabling_role_transitions_strands_coder_packets_in_review`
  会变红 —— 那正是提醒「可以把默认值改成 True 了」的信号。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from codentum_contracts.state import (
    EvidenceRef,
    Acceptance,
    BudgetGrant,
    ModelRouting,
    PacketId,
    Provenance,
    WorkPacket,
)
from codentum_control_plane.state_machine import TransitionTable
from codentum_engine.service import EngineConfig, EngineService
from codentum_roles.loader import load_builtin_role_specs


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    return root


def _reviewed_packet(pid: str) -> WorkPacket:
    """一个已经干完活、拿着真实证据、停在 review 的 coder packet。"""

    return WorkPacket(
        id=PacketId(pid),
        kind="impl",
        state="review",
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
        # ★ 非 sys: 前缀的真实证据 —— 08-10 修掉的那个洞要求这个
        evidence=(EvidenceRef("file:model/result.json"),),
        provenance=Provenance(createdBy= "intake", createdAt= "2026-08-10T00:00:00Z", parent= None),
    )


def _seed(project: Path, packet: WorkPacket) -> Path:
    state_dir = project / ".codentum"
    (state_dir / "packets").mkdir(parents=True, exist_ok=True)
    from codentum_contracts.state import dump_state

    (state_dir / "packets" / f"{packet.id}.json").write_text(
        json.dumps(dump_state(packet), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return state_dir


def _run(project: Path, *, enforce: bool) -> WorkPacket:
    service = EngineService(
        EngineConfig(project_root=project, enforce_role_transitions=enforce)
    )
    loop = service._build_loop()  # noqa: SLF001
    loop.load_state()
    loop.run_until_stable(max_ticks=10)
    loop.save_state()
    return next(iter(loop.packets.values()))


def test_the_transition_table_itself_is_right(project: Path) -> None:
    """★ 先证明表没错，再说 reconcile 用错了它。

    否则下面那条「打开就卡住」会被读成「表有问题」，而结论正好相反。
    """

    table = TransitionTable(load_builtin_role_specs())
    assert not table.check(
        role="coder", current="review", target="accepted", evidence=(EvidenceRef("file:x"),)
    ).allowed
    assert table.check(
        role="reviewer", current="review", target="accepted", evidence=(EvidenceRef("file:x"),)
    ).allowed


def test_enabling_role_transitions_strands_coder_packets_in_review(project: Path) -> None:
    """★ 打开之后，一个证据齐全、活已干完的 coder packet 会永远停在 review。

    这不是「护栏起作用了」，是**没有任何角色能推进它** ——
    reviewer 能做这个转换，但 reconcile 问的是 packet 自己的 role。

    修好之后这条会变红：那时把 EngineConfig.enforce_role_transitions
    的默认值改成 True，并删掉这条测试。
    """

    _seed(project, _reviewed_packet("wp-stranded01"))
    packet = _run(project, enforce=True)
    assert packet.state == "review", "如果它变成了 accepted，说明查询方式已经修好了"


def test_without_the_table_the_same_packet_is_accepted(project: Path) -> None:
    """★ 对照组。没有它的话，上面那条用「永远返回 review」也能绿。

    这里同时证明另一件事：引擎装配的门禁是**通的** ——
    packet 能走到 accepted，说明 gate_runner 不是摆设。
    """

    _seed(project, _reviewed_packet("wp-accepted01"))
    packet = _run(project, enforce=False)
    assert packet.state == "accepted"


def test_gates_still_reject_control_plane_bookkeeping_as_evidence(project: Path) -> None:
    """★ 引擎默认装了 gate_runner，08-10 修掉的那个洞不能在这条路径上复活。

    `sys:` 前缀是控制面自己的簿记 —— 「拿到过锁」不等于「活干完了」。
    """

    stranded = _reviewed_packet("wp-sysonly001").model_copy(
        update={"evidence": ("sys:lock:wp-sysonly001:1",)}
    )
    _seed(project, stranded)
    packet = _run(project, enforce=False)
    assert packet.state == "review", "只有 sys: 簿记的 packet 不得被自动验收"
