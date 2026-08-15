"""角色状态转换表：从「打开就死锁」到「打开是默认」。

════════════════════════════════════════════════════════════════
 这份文件记录的是一次**范畴错误**的发现与修复
════════════════════════════════════════════════════════════════

原先 `EngineConfig.enforce_role_transitions` 默认 False，理由是：
打开之后 coder 的 packet 会**永远停在 review**。

  - `reviewer` 声明了 `review → accepted`（requiresGate: review）
  - `integrator` 也声明了（requiresGate: self-test）
  - `coder` 没有 —— 它只有 `running → review` / `running → blocked`
  - 而 `_try_review_to_accepted` 用 **`role=packet.role`** 去查表

于是对 coder 的 packet，表的回答是「不允许」。

★ 表本身一直是对的。错在**提问的方式**：
  契约对 `RoleSpec.transitions` 的定义是「此角色可**触发**的状态转换」——
  role 是触发者，而调和循环**不是角色**，它在门禁通过后代为应用。
  拿 packet 自己的 role 去问「你能不能触发签字」，问的是错的人 ——
  而它恰好把「不能给自己签字」变成了「**没有人能签字**」。

════════════════════════════════════════════════════════════════
 修法：补一个系统侧查询，而不是在入口层绕过
════════════════════════════════════════════════════════════════

`TransitionTable.check_system()`：

    签字人 = 声明该转移的角色 − packet 自己的角色

这是 **I2 在状态机层的落点**，与准入层的 `check_self_review` 是同一条
不变量的两个强制点。门禁由签字人声明（reviewer 要 review、
integrator 要 self-test，两者都对，因为触发者不同）。

2026-08-15 起 `enforce_role_transitions` 默认 **True**。

★ 原先钉住缺口的那条测试当时写着「修好之后这条会变红」——
  它如期变红了，于是按它自己的设计改写成正向断言。
  **一条钉住缺口的测试，缺口修好时就该红。**
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


def _run(project: Path, *, enforce: bool, approve: bool = False) -> WorkPacket:
    """跑到稳定态并返回那个 packet。

    ★ `approve=True`：夹具用的是 `acceptance.kind="manual"`（那样不必铺一个
      真实工作区），而 2026-08-15 起 **manual 验收必须 operator 显式批准** ——
      此前它靠「有证据」就能过，那是个真实的洞（有证据 ≠ 有人签字）。

      这两条测试的对象是**角色转换表**，人工批准只是它的合法前置。
      不补批准的话，packet 会停在 review，而红的原因与转换表无关 ——
      那样这组测试测的就不是它声称测的东西了。

    ★ 默认 **False**，只在真的需要的那两条里打开。
      第一版默认 True，结果把 `test_gates_still_reject_control_plane_bookkeeping_as_evidence`
      弄红了 —— 那条测的是「只有 sys: 簿记时不得验收」，
      而无差别批准正好绕过了它。**夹具的默认值不该替某条测试做决定。**
    """

    service = EngineService(
        EngineConfig(project_root=project, enforce_role_transitions=enforce)
    )
    loop = service._build_loop()  # noqa: SLF001
    loop.load_state()
    if approve:
        for packet_id in loop.packets:
            loop.approve(packet_id, note="测试夹具：manual 验收的人工批准")
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


def test_enabling_role_transitions_no_longer_strands_coder_packets(project: Path) -> None:
    """★ 2026-08-15：这条原本断言「打开就会永远停在 review」，**现在反过来了**。

    它当时的断言消息写着「如果它变成了 accepted，说明查询方式已经修好了」——
    修好之后它如期变红，于是按它自己的设计改写成正向断言。
    **一条钉住缺口的测试，缺口修好时就该红。**

    修的是范畴错误：`RoleSpec.transitions` 的契约定义是
    「此角色可**触发**的转换」，而调和循环不是角色 ——
    它在门禁通过后代为应用。用 `check_system()` 之后，
    签字人 = 声明者 − packet 自己的角色。
    """

    _seed(project, _seeded := _reviewed_packet("wp-stranded01"))
    packet = _run(project, enforce=True, approve=True)
    assert packet.state == "accepted", "打开角色转换表之后，coder 的 packet 仍然推不动"


def test_a_reviewer_packet_cannot_sign_itself(project: Path) -> None:
    """★ I2 在状态机层的落点：签字人必须是**别的**角色。

    `reviewer` 自己声明了 review→accepted。如果签字人集合不减掉
    packet 自己的角色，一个 reviewer 的 packet 就能自己给自己签字 ——
    而那正是打开这张表本来要防的事。

    ★ 这条与准入层的 `check_self_review` 是**同一条不变量的两个强制点**：
      那边管「不能给自己定验收标准」，这边管「不能给自己签字」。
    """

    from codentum_control_plane.state_machine import TransitionTable
    from codentum_roles.loader import load_builtin_role_specs

    table = TransitionTable(load_builtin_role_specs())

    # ★ 用 pending→ready：它**只有 planner** 声明。
    #   一个 planner 的 packet 因此没有别的签字人 —— 必须被拒。
    #
    #   （review→accepted 测不出这条：它有 reviewer 与 integrator 两个声明者，
    #     reviewer 的 packet 会由 integrator 签，自签本来就轮不上。
    #     选一个单声明者的转移，这条不变量才真的被暴露出来。）
    denied = table.check_system(
        packet_role="planner",
        acceptance_author="qa",
        current="pending",
        target="ready",
        evidence=(EvidenceRef("file:x"),),
    )
    assert not denied.allowed
    assert "不能给自己的活签字" in denied.detail

    # 对照：别人的 packet 由 planner 签，是允许的
    allowed = table.check_system(
        packet_role="coder",
        acceptance_author="qa",
        current="pending",
        target="ready",
        evidence=(EvidenceRef("file:x"),),
    )
    assert allowed.allowed, "没有对照的话，上面那条用「永远拒绝」也能绿"


def test_the_gate_comes_from_the_signer_not_the_packet(project: Path) -> None:
    """★ reviewer 要 `review` 门、integrator 要 `self-test` 门 —— 两者都对。

    契约说 role 是**触发者**，所以「该过哪道门」由触发者决定，不由 packet 决定。
    这条钉住的是：签字人挑选是**显式且确定**的，不是碰巧。
    """

    from codentum_control_plane.state_machine import TransitionTable
    from codentum_roles.loader import load_builtin_role_specs

    table = TransitionTable(load_builtin_role_specs())
    evidence = (EvidenceRef("file:x"),)

    # 验收作者是 reviewer 且它有签字权 → 用 reviewer 的门
    by_reviewer = table.check_system(
        packet_role="coder", acceptance_author="reviewer",
        current="review", target="accepted", evidence=evidence,
    )
    assert by_reviewer.allowed and by_reviewer.requires_gate == "review"

    # 验收作者是 qa（没有签字权）→ 回退到确定的排序首位，而不是随机
    by_fallback = table.check_system(
        packet_role="coder", acceptance_author="qa",
        current="review", target="accepted", evidence=evidence,
    )
    assert by_fallback.allowed
    assert by_fallback.requires_gate == "self-test", "回退必须是确定的（sorted 首位 = integrator）"


def test_without_the_table_the_same_packet_is_accepted(project: Path) -> None:
    """★ 对照组。没有它的话，上面那条用「永远返回 review」也能绿。

    这里同时证明另一件事：引擎装配的门禁是**通的** ——
    packet 能走到 accepted，说明 gate_runner 不是摆设。
    """

    _seed(project, _reviewed_packet("wp-accepted01"))
    packet = _run(project, enforce=False, approve=True)
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
