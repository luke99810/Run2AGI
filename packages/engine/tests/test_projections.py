"""调度与流动投影的判据。

★ 这两个文件是**给人看着做决定**的。一个编出来的瓶颈会把人引向错误的地方，
  比没有瓶颈信息更糟。所以这组测试守的核心是一句话：
  **每个数字都能追溯到真实事件，算不出的就不写。**
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from codentum_contracts.state import (
    Acceptance,
    BudgetGrant,
    PacketId,
    Provenance,
    WorkPacket,
)
from codentum_engine.projections import write_flow, write_scheduling

# ══════════════════════════════════════════════════════════════
#  C 的读取侧校验规则（镜像）
#
#  ★ 来源：origin/role-c/desktop-v2 的
#    packages/desktop/data/directory-state-source.ts
#    （isSchedulingProjection / isFlowProjection 及其下属守卫）
#
#  ★ 这是一份**跨语言接缝的镜像**，天然有漂移风险 ——
#    这正是本仓库既有的做法（写侧 Pydantic、读侧手写守卫、
#    tests/e2e 设跨语言用例盯接缝）。
#    在这里镜像一份的理由是：写出来却过不了对方的校验，
#    等于没写 —— 而那种失败在 Python 侧不会有任何信号。
# ══════════════════════════════════════════════════════════════

_PACKET_STATES = {
    "pending", "ready", "running", "review", "accepted", "rejected", "blocked", "abandoned",
}


def _assert_scheduling_shape(data: Any) -> None:
    assert data["schemaVersion"] == 1
    wip = data["wipLimits"]
    assert isinstance(wip, dict)
    for state, limit in wip.items():
        assert state in _PACKET_STATES, f"未知状态：{state}"
        assert isinstance(limit, int) and limit >= 0
    if "revision" in data:
        assert isinstance(data["revision"], int) and data["revision"] >= 0
    for key in ("readyQueue", "criticalPath"):
        if key in data:
            assert all(isinstance(x, str) for x in data[key])


def _assert_flow_shape(data: Any) -> None:
    assert data["schemaVersion"] == 1
    if "efficiency" in data:
        assert 0.0 <= data["efficiency"] <= 1.0
    for stage in data["stages"]:
        assert stage["state"] in _PACKET_STATES
        assert isinstance(stage["packetCount"], int) and stage["packetCount"] >= 0
        for key in ("waitP50Ms", "waitP80Ms"):
            if key in stage:
                assert stage[key] >= 0
    for flow in data["packets"]:
        assert isinstance(flow["packetId"], str)
        assert flow["totalCycleMs"] >= 0
        if "efficiency" in flow:
            assert 0.0 <= flow["efficiency"] <= 1.0
        for seg in flow["segments"]:
            assert seg["state"] in _PACKET_STATES
            assert seg["kind"] in {"waiting", "value"}
            assert seg["durationMs"] >= 0
    if "bottleneck" in data:
        bottleneck = data["bottleneck"]
        assert bottleneck["state"] in _PACKET_STATES
        assert bottleneck["waitP80Ms"] >= 0
        assert isinstance(bottleneck["affectedPackets"], int)
    for andon in data["andons"]:
        assert isinstance(andon["id"], str)
        assert isinstance(andon["packetId"], str)
        assert andon["severity"] in {"warning", "critical"}
        assert isinstance(andon["reason"], str)
        assert isinstance(andon["at"], str)


# ══════════════════════════════════════════════════════════════


def _packet(pid: str, state: str, *, deps: tuple[str, ...] = (), created: str = "2026-08-15T10:00:00Z") -> WorkPacket:
    return WorkPacket(
        id=PacketId(pid),
        kind="impl",
        state=state,  # type: ignore[arg-type]
        role="coder",
        ownsPaths=(f"workspace/{pid}/",),
        readsPaths=(),
        deps=tuple(PacketId(d) for d in deps),
        acceptance=Acceptance(kind="test", predicate="pytest", authoredBy="qa"),
        budget=BudgetGrant(
            currency="CNY", limitCny=1.0, spentCny=0.0, degradationChain=("drop_semantic",)
        ),
        attempts=0,
        evidence=(),
        provenance=Provenance(createdBy="planner", createdAt=created),
    )


def _write_decisions(state_dir: Path, rows: list[dict[str, Any]]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "decisions.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )


def _decision(pid: str, at: str, frm: str, to: str) -> dict[str, Any]:
    return {
        "at": at, "actor": "coder", "action": "packet_transitioned",
        "packetId": pid, "reasonCode": f"Reconcile.{frm}_to_{to}",
    }


# ══════════════════════════════════════════════════════════════
#  scheduling.json
# ══════════════════════════════════════════════════════════════


def test_wip_limit_is_only_written_when_actually_enforced(tmp_path: Path) -> None:
    """★ 没有真正执行的限制，就不该往文件里写数字。

    写了就是「声明了但没人执行」—— 而桌面端会照着它渲染，
    使用者会以为系统在按这个上限调度。
    C 的契约也是这么定的：未提供的状态显示「—」，**C 不推算默认值**。
    """

    packets = {PacketId("wp-aaa001"): _packet("wp-aaa001", "ready")}

    write_scheduling(tmp_path, packets, max_running=None, revision=1)
    assert json.loads((tmp_path / "scheduling.json").read_text("utf-8"))["wipLimits"] == {}

    write_scheduling(tmp_path, packets, max_running=3, revision=1)
    data = json.loads((tmp_path / "scheduling.json").read_text("utf-8"))
    assert data["wipLimits"] == {"running": 3}
    _assert_scheduling_shape(data)


def test_ready_queue_matches_the_actual_pull_order(tmp_path: Path) -> None:
    """★ 报一个和实际不同的顺序，比不报更误导。

    reconcile 的拉取顺序是 `sorted(self._packets)` —— 界面上排在最前的那个
    必须就是下一个真的会跑的。
    """

    packets = {
        PacketId(pid): _packet(pid, state)
        for pid, state in [("wp-aaa00c", "ready"), ("wp-aaa00a", "ready"), ("wp-aaa00b", "running")]
    }
    write_scheduling(tmp_path, packets, max_running=2, revision=1)

    data = json.loads((tmp_path / "scheduling.json").read_text("utf-8"))
    assert data["readyQueue"] == ["wp-aaa00a", "wp-aaa00c"], "顺序必须与 sorted(packets) 一致"
    assert "wp-aaa00b" not in data["readyQueue"], "running 的不该出现在 ready 队列里"


def test_critical_path_follows_the_dependency_chain(tmp_path: Path) -> None:
    """★ 由 A 算而不是 C 算：C 手上只有当前快照。

    两边各算一遍必然在某个时刻不一致，**而不一致时没有任何东西会报错**。
    """

    packets = {
        PacketId("wp-aaa00a"): _packet("wp-aaa00a", "pending"),
        PacketId("wp-aaa00b"): _packet("wp-aaa00b", "pending", deps=("wp-aaa00a",)),
        PacketId("wp-aaa00c"): _packet("wp-aaa00c", "pending", deps=("wp-aaa00b",)),
        PacketId("wp-aaa00x"): _packet("wp-aaa00x", "pending"),
    }
    write_scheduling(tmp_path, packets, max_running=2, revision=1)

    data = json.loads((tmp_path / "scheduling.json").read_text("utf-8"))
    assert data["criticalPath"] == ["wp-aaa00a", "wp-aaa00b", "wp-aaa00c"]


# ══════════════════════════════════════════════════════════════
#  flow.json
# ══════════════════════════════════════════════════════════════


def test_durations_come_from_the_decision_log(tmp_path: Path) -> None:
    """★ 时长全部来自 decisions.jsonl 的时间戳，一条也不猜。

    C 的契约特意写明「C 不使用文件修改时间推算」—— 用 mtime 推时长
    会在任何一次无关重写后给出错误答案，而且不会报错。
    """

    packets = {PacketId("wp-aaa001"): _packet("wp-aaa001", "running", created="2026-08-15T10:00:00Z")}
    _write_decisions(tmp_path, [
        _decision("wp-aaa001", "2026-08-15T10:00:30Z", "pending", "ready"),   # pending 30s
        _decision("wp-aaa001", "2026-08-15T10:01:30Z", "ready", "running"),   # ready 60s
    ])

    write_flow(tmp_path, packets)
    data = json.loads((tmp_path / "flow.json").read_text("utf-8"))
    _assert_flow_shape(data)

    (flow,) = data["packets"]
    by_state = {seg["state"]: seg for seg in flow["segments"]}
    assert by_state["pending"]["durationMs"] == 30_000
    assert by_state["ready"]["durationMs"] == 60_000
    assert by_state["pending"]["kind"] == "waiting"
    assert by_state["running"]["kind"] == "value", "只有 running 算增值"


def test_first_segment_starts_at_packet_creation(tmp_path: Path) -> None:
    """★ 第一段的起点是 `provenance.createdAt`。

    第一条决策记录的是**离开** pending 的时刻。少了这一段，
    「等依赖等了多久」会整段丢失 —— 而那常常是最长的一段。
    """

    packets = {PacketId("wp-aaa001"): _packet("wp-aaa001", "ready", created="2026-08-15T10:00:00Z")}
    _write_decisions(tmp_path, [_decision("wp-aaa001", "2026-08-15T10:05:00Z", "pending", "ready")])

    write_flow(tmp_path, packets)
    (flow,) = json.loads((tmp_path / "flow.json").read_text("utf-8"))["packets"]
    pending = next(seg for seg in flow["segments"] if seg["state"] == "pending")
    assert pending["durationMs"] == 300_000, "等依赖那 5 分钟被丢掉了"


def test_no_decision_log_yields_empty_not_zero(tmp_path: Path) -> None:
    """★ 「0 秒等待」和「不知道等了多久」是两件相反的事。

    没有历史时写零值，会让界面显示一个完美的流动效率 ——
    那是本项目一路在拆的那种「零输入的绿灯」。
    """

    write_flow(tmp_path, {PacketId("wp-aaa001"): _packet("wp-aaa001", "ready")})
    data = json.loads((tmp_path / "flow.json").read_text("utf-8"))

    assert data["packets"] == []
    assert data["stages"] == []
    assert "efficiency" not in data, "没有数据却报了一个效率值"
    assert "bottleneck" not in data, "没有数据却报了一个瓶颈"
    _assert_flow_shape(data)


def test_bottleneck_is_the_longest_waiting_stage(tmp_path: Path) -> None:
    packets = {
        PacketId("wp-aaa001"): _packet("wp-aaa001", "running", created="2026-08-15T10:00:00Z"),
        PacketId("wp-aaa002"): _packet("wp-aaa002", "running", created="2026-08-15T10:00:00Z"),
    }
    _write_decisions(tmp_path, [
        # 两个 packet 都在 review 等很久 —— review 应当成为瓶颈
        _decision("wp-aaa001", "2026-08-15T10:00:10Z", "pending", "ready"),
        _decision("wp-aaa001", "2026-08-15T10:00:20Z", "ready", "review"),
        _decision("wp-aaa001", "2026-08-15T10:10:20Z", "review", "running"),
        _decision("wp-aaa002", "2026-08-15T10:00:10Z", "pending", "ready"),
        _decision("wp-aaa002", "2026-08-15T10:00:20Z", "ready", "review"),
        _decision("wp-aaa002", "2026-08-15T10:12:20Z", "review", "running"),
    ])

    write_flow(tmp_path, packets)
    data = json.loads((tmp_path / "flow.json").read_text("utf-8"))
    assert data["bottleneck"]["state"] == "review"
    assert data["bottleneck"]["affectedPackets"] == 2
    _assert_flow_shape(data)


def test_blocked_packet_raises_a_critical_andon(tmp_path: Path) -> None:
    """★ 安灯的 reason 里必须带实测数字。

    「等待过久」四个字没法判断严重程度，人只能去翻日志 —— 那等于没告警。
    """

    packets = {PacketId("wp-aaa001"): _packet("wp-aaa001", "blocked", created="2026-08-15T10:00:00Z")}
    _write_decisions(tmp_path, [_decision("wp-aaa001", "2026-08-15T10:00:10Z", "ready", "blocked")])

    write_flow(tmp_path, packets)
    (andon,) = json.loads((tmp_path / "flow.json").read_text("utf-8"))["andons"]
    assert andon["severity"] == "critical"
    assert andon["packetId"] == "wp-aaa001"


def test_writes_are_atomic(tmp_path: Path) -> None:
    """★ C 的契约要求原子替换。

    直接覆写时桌面端可能读到写了一半的 JSON —— 它会判为坏文件并拒绝，
    于是**上一份一致快照也一起丢了**。
    """

    packets = {PacketId("wp-aaa001"): _packet("wp-aaa001", "ready")}
    write_scheduling(tmp_path, packets, max_running=1, revision=1)
    write_flow(tmp_path, packets)

    assert not list(tmp_path.glob("*.tmp")), "临时文件没有被替换掉"
    json.loads((tmp_path / "scheduling.json").read_text("utf-8"))
    json.loads((tmp_path / "flow.json").read_text("utf-8"))
