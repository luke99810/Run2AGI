"""Planner 判据 —— 一个需求拆成多个 packet。

★ 这一层守的是**三条硬约束**，每条都必须有会红的证据：

  1. 路径两两不相交  —— 相交即 I1 违规
  2. QA 排在 impl 之前 —— 对「判据套利」的结构性修复
  3. 依赖图无环      —— 有环则永远推不动

前两条尤其重要：**它们的违规不会在拆解时报错**，
而是在运行时表现成锁冲突或验收被绕过。
"""

from __future__ import annotations

import pytest
from codentum_contracts.state import PacketId, WorkPacket

from codentum_engine.planner import (
    MAX_TASKS,
    PlannedTask,
    build_packets_from_plan,
    parse_plan,
    plan_prompt,
)

# ══════════════════════════════════════════════════════════════
#  解析
# ══════════════════════════════════════════════════════════════


def test_plain_json_is_parsed() -> None:
    tasks = parse_plan('{"tasks":[{"title":"做 API","detail":"实现增删改查","module":"api"}]}')
    assert len(tasks) == 1
    assert tasks[0].module == "api"


def test_surrounding_prose_is_tolerated() -> None:
    """★ 模型很常见地在 JSON 前后加自然语言。

    整串 json.loads 必然失败，而中间那个对象其实是好的 ——
    与工具调用参数的处理是同一条经验。
    """

    raw = '好的，这是拆解结果：\n{"tasks":[{"title":"T","detail":"D","module":"api"}]}\n希望有帮助。'
    assert parse_plan(raw)[0].module == "api"


def test_markdown_fence_is_stripped() -> None:
    raw = '```json\n{"tasks":[{"title":"T","detail":"D","module":"api"}]}\n```'
    assert parse_plan(raw)[0].module == "api"


def test_duplicate_modules_are_renamed_not_dropped() -> None:
    """★ 模块重名 → 路径相交 → I1 违规。

    这里改名而不是丢弃：**丢弃会让需求的一部分凭空消失**，
    而消失是那种没有任何测试会红的失败。
    """

    tasks = parse_plan(
        '{"tasks":['
        '{"title":"A","detail":"a","module":"api"},'
        '{"title":"B","detail":"b","module":"api"},'
        '{"title":"C","detail":"c","module":"API"}]}'
    )
    assert len(tasks) == 3, "重名任务被丢弃了"
    assert len({t.module for t in tasks}) == 3, "改名后仍有重复"


def test_too_many_tasks_are_truncated_loudly(caplog: pytest.LogCaptureFixture) -> None:
    """★ 截断必须出声。静默丢弃会让需求的一部分消失得无影无踪。"""

    import logging

    items = ",".join(
        f'{{"title":"T{i}","detail":"d","module":"m{i}"}}' for i in range(MAX_TASKS + 4)
    )
    with caplog.at_level(logging.WARNING):
        tasks = parse_plan(f'{{"tasks":[{items}]}}')
    assert len(tasks) == MAX_TASKS
    assert any("截断" in r.message for r in caplog.records), "截断了但没出声"


def test_unusable_output_raises_rather_than_returning_empty() -> None:
    """★ 拆不出任务要抛错，不能返回空列表。

    返回空列表的话，上游会以为「这个需求不需要做任何事」而直接成功 ——
    那是最糟的失败形态。
    """

    with pytest.raises(ValueError, match="没有"):
        parse_plan('{"tasks":[]}')
    with pytest.raises(ValueError, match="JSON"):
        parse_plan("我不知道该怎么拆")


# ══════════════════════════════════════════════════════════════
#  硬约束一：路径两两不相交
# ══════════════════════════════════════════════════════════════


def _sample_tasks() -> tuple[PlannedTask, ...]:
    return (
        PlannedTask(title="API", detail="增删改查", module="api"),
        PlannedTask(title="存储", detail="SQLite", module="storage"),
        PlannedTask(title="命令行", detail="CLI 入口", module="cli"),
    )


def _packets() -> tuple[WorkPacket, ...]:
    return build_packets_from_plan(
        _sample_tasks(),
        requirement="做一个待办应用",
        model="qwen-coder-plus-1106",
        effort="medium",
        total_budget_cny=7.0,
    )


def _overlaps(a: str, b: str) -> bool:
    return a.startswith(b) or b.startswith(a)


def test_owns_paths_never_overlap() -> None:
    """★ 相交即 I1 违规，而违规**不会在拆解时报错** ——
    它在运行时表现成锁冲突，那时已经花过钱了。"""

    packets = _packets()
    # 集成 packet 独占 workspace/，它与其他 packet 是依赖关系而非并行关系，
    # 所以只检查会并行推进的那些（test 与 impl）。
    parallel = [p for p in packets if p.kind != "integrate"]
    for i, left in enumerate(parallel):
        for right in parallel[i + 1 :]:
            for lp in left.ownsPaths:
                for rp in right.ownsPaths:
                    assert not _overlaps(lp, rp), f"{left.id} 与 {right.id} 路径相交：{lp} / {rp}"


def test_impl_can_read_but_not_write_the_tests() -> None:
    """★ impl 读得到验收测试，却不能改它 —— I2 在路径层面的落实。

    能改验收标准的执行者，等于自己给自己签字。
    """

    packets = _packets()
    impl = next(p for p in packets if p.kind == "impl")
    tests_path = next(p for p in packets if p.kind == "test").ownsPaths[0]

    assert tests_path in impl.readsPaths, "impl 看不到验收标准"
    for owned in impl.ownsPaths:
        assert not tests_path.startswith(owned), "impl 能写验收测试 —— 判据可被套利"


# ══════════════════════════════════════════════════════════════
#  硬约束二：QA 排在 impl 之前
# ══════════════════════════════════════════════════════════════


def test_every_impl_depends_on_its_test_packet() -> None:
    """★ 先出题、后做题。

    这是对「判据套利」的**结构性**修复：08-13 实测中模型写出
    `assert True` 满足自己的验收标准，因为出题与做题是同一方。
    元数据字段 `authoredBy` 只能保证名义隔离。
    """

    packets = _packets()
    tests = {p.id: p for p in packets if p.kind == "test"}
    impls = [p for p in packets if p.kind == "impl"]

    assert len(impls) == 3
    for impl in impls:
        assert impl.deps, f"{impl.id} 没有依赖任何测试 packet"
        assert any(dep in tests for dep in impl.deps), f"{impl.id} 的依赖里没有 test packet"


def test_test_packets_are_authored_by_a_different_role() -> None:
    packets = _packets()
    for packet in packets:
        assert packet.acceptance.authoredBy != packet.role, (
            f"{packet.id}（{packet.role}）在给自己定验收标准"
        )


def test_roles_come_from_a_table_not_from_the_model() -> None:
    """★ 角色决定权限与工具面，是**安全边界**，不该由自然语言输出决定。"""

    packets = _packets()
    by_kind = {p.kind: p.role for p in packets}
    assert by_kind["test"] == "qa"
    assert by_kind["impl"] == "coder"
    assert by_kind["integrate"] == "integrator"


# ══════════════════════════════════════════════════════════════
#  硬约束三：依赖图无环，且能真的推进
# ══════════════════════════════════════════════════════════════


def test_dependency_graph_is_acyclic() -> None:
    packets = _packets()
    by_id = {p.id: p for p in packets}
    visiting: set[PacketId] = set()
    done: set[PacketId] = set()

    def walk(pid: PacketId) -> None:
        if pid in done:
            return
        assert pid not in visiting, f"依赖成环，经过 {pid}"
        visiting.add(pid)
        for dep in by_id[pid].deps:
            if dep in by_id:
                walk(dep)
        visiting.discard(pid)
        done.add(pid)

    for packet in packets:
        walk(packet.id)


def test_integration_waits_for_every_impl() -> None:
    """★ 集成必须等所有 impl —— 少等一个就等于集成了一半。"""

    packets = _packets()
    integrate = next(p for p in packets if p.kind == "integrate")
    impl_ids = {p.id for p in packets if p.kind == "impl"}
    assert impl_ids <= set(integrate.deps), "集成没有等齐所有实现"


def test_the_plan_is_actually_schedulable() -> None:
    """★ 最重要的一条：这批 packet 放进调和循环能不能真的推进？

    前面几条断言的是结构，这条断言的是**结构可用** ——
    一个结构完美但推不动的计划毫无价值。
    """

    packets = _packets()
    states = {p.id: "pending" for p in packets}

    progressed = True
    rounds = 0
    while progressed and rounds < 20:
        progressed = False
        rounds += 1
        for packet in packets:
            if states[packet.id] != "pending":
                continue
            if all(states.get(dep) == "accepted" for dep in packet.deps):
                states[packet.id] = "accepted"
                progressed = True

    assert all(state == "accepted" for state in states.values()), (
        f"有 packet 永远推不动：{[pid for pid, s in states.items() if s != 'accepted']}"
    )
    # ★ 层数：test → impl → integrate，三轮足够
    assert rounds <= 4, f"用了 {rounds} 轮才推完，依赖层次异常"


# ══════════════════════════════════════════════════════════════
#  预算
# ══════════════════════════════════════════════════════════════


def test_budget_is_split_across_all_packets_including_integration() -> None:
    """★ 漏算集成 packet 会导致最后一步没钱可花，
    而那时前面的钱已经花掉了。"""

    packets = _packets()
    total = sum(p.budget.limitCny for p in packets)
    assert total == pytest.approx(7.0), f"预算总额漂移：{total}"
    assert all(p.budget.limitCny > 0 for p in packets)


def test_empty_task_list_is_rejected() -> None:
    with pytest.raises(ValueError, match="为空"):
        build_packets_from_plan(
            (), requirement="x", model="m", effort="medium", total_budget_cny=1.0
        )


# ══════════════════════════════════════════════════════════════
#  提示词
# ══════════════════════════════════════════════════════════════


def test_prompt_states_the_hard_constraints() -> None:
    """★ 模块名互不相同是下游的硬约束，提示词必须说出来 ——
    不说而指望模型碰巧做对，是把不变量交给它守。"""

    prompt = plan_prompt("做一个待办应用")
    assert "做一个待办应用" in prompt
    assert "互不相同" in prompt
    assert str(MAX_TASKS) in prompt
