"""桌面端投影：`.codentum/flow.json`（scheduling.json 由控制平面写）。

════════════════════════════════════════════════════════════════
 ★ 这两个文件为什么必须由 A 算，而不是 C 推
════════════════════════════════════════════════════════════════

C 的集成契约写得很明确：

  · `wipLimits` 未提供的状态显示 `—`，**C 不推算默认值**
  · `readyQueue` 是 A 的确定性拉式队列，C 只用它排序
  · `criticalPath` 是 A 的计划结果，**C 不根据前端依赖图重复计算**
  · 流动效率/等待 p80/瓶颈由 A 从权威状态算，**C 不使用文件修改时间推算**

理由是同一条：**桌面端只能看到文件的当前样子，看不到它是怎么变成这样的。**
用 mtime 推时长，会在任何一次无关的重写后给出错误答案 —— 而且不会报错。

════════════════════════════════════════════════════════════════
 ★ 每个数字都必须能追溯到真实事件，算不出的**就不写**
════════════════════════════════════════════════════════════════

这两个文件是给人看着做决定的。一个编出来的瓶颈会把人引向错误的地方，
比没有瓶颈信息更糟。所以：

  · `scheduling.json` 由**控制平面**写（`ReconcileLoop._write_scheduling`）——
    WIP 上限只有在那里才是真正被执行的。这个模块只负责 `flow.json`。
  · 时长全部来自 `decisions.jsonl` 的时间戳，一条也不猜。
  · 没有决策历史时，`flow.json` 写空结构而不是零值 ——
    「0 秒等待」和「不知道等了多久」是两件相反的事。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from codentum_contracts.state import PacketId, WorkPacket

__all__ = ["write_flow"]

logger = logging.getLogger(__name__)

_VALUE_STATES: frozenset[str] = frozenset({"running"})
"""真正在增值的状态。其余非终态一律算等待。

★ 这个划分是精益的定义，不是主观取舍：只有 worker 真的在干活时价值才在增加。
  `review` 看起来「在做事」，但那是**等门禁**，packet 本身没有变化。
"""

_TERMINAL_STATES: frozenset[str] = frozenset({"accepted", "abandoned", "rejected"})

ANDON_WAIT_THRESHOLD_MS = 5 * 60_000
"""触发安灯的等待时长阈值。

★ 取 5 分钟的理由：单次模型调用超时是 180 秒（`model_timeout_seconds`），
  所以一个 **running** 的 packet 停 3 分钟是正常的。而这个阈值只作用于
  **等待**状态（pending / ready / review）—— 那些状态里没有任何东西在跑，
  停 5 分钟意味着确实卡住了。

★ 阈值写成常量并在这里说明理由，是因为它是**唯一一个不由数据决定的数**。
  藏在代码里的魔数会让「为什么这条报警了」变成无法回答的问题。
"""


def _parse(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _percentile(values: Sequence[float], pct: float) -> float | None:
    """最近秩百分位。

    ★ 不做插值：样本量小的时候插值会造出**一个没有发生过的数**。
      这里的读者要拿它做决定，宁可给一个真实观测值。
    """

    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(-(-pct * len(ordered) // 100))))
    return ordered[rank - 1]


@dataclass(frozen=True, slots=True)
class _Segment:
    state: str
    start: datetime
    end: datetime | None

    @property
    def duration_ms(self) -> float:
        end = self.end or datetime.now(UTC)
        return max(0.0, (end - self.start).total_seconds() * 1000.0)


def _segments_by_packet(
    decisions: Sequence[Mapping[str, Any]], packets: Mapping[PacketId, WorkPacket]
) -> dict[str, list[_Segment]]:
    """把决策流水还原成「每个 packet 在每个状态停了多久」。

    ★ 第一段的起点用 `provenance.createdAt` —— packet 建出来就处于 pending，
      而第一条决策记录的是**离开** pending 的时刻。少了这一段，
      「等依赖等了多久」会整段丢失，而那常常是最长的一段。
    """

    events: dict[str, list[tuple[datetime, str, str]]] = {}
    for row in decisions:
        pid = str(row.get("packetId") or "")
        at = _parse(str(row.get("at") or ""))
        reason = str(row.get("reasonCode") or "")
        if not pid or at is None or not reason.startswith("Reconcile."):
            continue
        transition = reason.removeprefix("Reconcile.")
        if "_to_" not in transition:
            continue
        from_state, _, to_state = transition.partition("_to_")
        events.setdefault(pid, []).append((at, from_state, to_state))

    result: dict[str, list[_Segment]] = {}
    for pid, rows in events.items():
        rows.sort(key=lambda item: item[0])
        packet = packets.get(PacketId(pid))
        started = _parse(packet.provenance.createdAt) if packet is not None else None
        segments: list[_Segment] = []

        cursor = started or rows[0][0]
        for at, from_state, to_state in rows:
            segments.append(_Segment(state=from_state, start=cursor, end=at))
            cursor = at
        # 末段：仍停在最后一个 to_state 上；终态不计入（它不再消耗时间）
        last_state = rows[-1][2]
        if last_state not in _TERMINAL_STATES:
            segments.append(_Segment(state=last_state, start=cursor, end=None))
        result[pid] = segments
    return result


def write_flow(state_dir: Path, packets: Mapping[PacketId, WorkPacket]) -> None:
    """从 `decisions.jsonl` 算出 `.codentum/flow.json`。"""

    decisions_path = state_dir / "decisions.jsonl"
    decisions: list[Mapping[str, Any]] = []
    if decisions_path.exists():
        for line in decisions_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                decisions.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    by_packet = _segments_by_packet(decisions, packets)

    packet_flows: list[dict[str, Any]] = []
    waits_by_state: dict[str, list[float]] = {}
    for pid in sorted(by_packet):
        segments = by_packet[pid]
        total = sum(seg.duration_ms for seg in segments)
        value = sum(seg.duration_ms for seg in segments if seg.state in _VALUE_STATES)
        rendered = [
            {
                "state": seg.state,
                "kind": "value" if seg.state in _VALUE_STATES else "waiting",
                "durationMs": round(seg.duration_ms),
                "startedAt": seg.start.isoformat().replace("+00:00", "Z"),
                **({"endedAt": seg.end.isoformat().replace("+00:00", "Z")} if seg.end else {}),
            }
            for seg in segments
        ]
        flow: dict[str, Any] = {
            "packetId": pid,
            "totalCycleMs": round(total),
            "segments": rendered,
        }
        if total > 0:
            flow["efficiency"] = min(1.0, max(0.0, value / total))
        packet_flows.append(flow)

        for seg in segments:
            if seg.state not in _VALUE_STATES and seg.state not in _TERMINAL_STATES:
                waits_by_state.setdefault(seg.state, []).append(seg.duration_ms)

    stages: list[dict[str, Any]] = []
    for state in sorted(waits_by_state):
        waits = waits_by_state[state]
        stage: dict[str, Any] = {
            "state": state,
            "packetCount": sum(1 for p in packets.values() if p.state == state),
        }
        p50 = _percentile(waits, 50)
        p80 = _percentile(waits, 80)
        if p50 is not None:
            stage["waitP50Ms"] = round(p50)
        if p80 is not None:
            stage["waitP80Ms"] = round(p80)
        stages.append(stage)

    total_all = sum(f["totalCycleMs"] for f in packet_flows)
    value_all = sum(
        seg["durationMs"]
        for f in packet_flows
        for seg in f["segments"]
        if seg["kind"] == "value"
    )

    payload: dict[str, Any] = {
        "schemaVersion": 1,
        "calculatedAt": _now_iso(),
        "stages": stages,
        "packets": packet_flows,
        "andons": _andons(by_packet, packets),
    }
    if total_all > 0:
        payload["efficiency"] = min(1.0, max(0.0, value_all / total_all))

    bottleneck = max(
        (s for s in stages if "waitP80Ms" in s), key=lambda s: s["waitP80Ms"], default=None
    )
    if bottleneck is not None:
        payload["bottleneck"] = {
            "state": bottleneck["state"],
            "waitP80Ms": bottleneck["waitP80Ms"],
            "affectedPackets": len(waits_by_state[bottleneck["state"]]),
            "recommendation": f"等待最久的环节是 {bottleneck['state']}，优先疏通它。",
        }

    _atomic_write(state_dir / "flow.json", payload)


def _andons(
    by_packet: Mapping[str, Sequence[_Segment]], packets: Mapping[PacketId, WorkPacket]
) -> list[dict[str, Any]]:
    """安灯：**只报有实测支撑的**两类。

    ★ 每条安灯的 reason 里都带上实测数字，而不是「等待过久」四个字。
      不带数字的告警没法判断严重程度，人只能去翻日志 —— 那等于没告警。
    """

    andons: list[dict[str, Any]] = []
    for pid in sorted(by_packet):
        packet = packets.get(PacketId(pid))
        if packet is None:
            continue

        if packet.state == "blocked":
            andons.append({
                "id": f"andon-blocked-{pid}",
                "packetId": pid,
                "severity": "critical",
                "reason": f"packet 处于 blocked，尝试次数 {packet.attempts}。",
                "consecutiveFailures": packet.attempts,
                "at": _now_iso(),
            })
            continue

        ongoing = [seg for seg in by_packet[pid] if seg.end is None]
        for seg in ongoing:
            if seg.state in _VALUE_STATES or seg.state in _TERMINAL_STATES:
                continue
            if seg.duration_ms >= ANDON_WAIT_THRESHOLD_MS:
                andons.append({
                    "id": f"andon-wait-{pid}-{seg.state}",
                    "packetId": pid,
                    "severity": "warning",
                    "reason": (
                        f"在 {seg.state} 已等待 {round(seg.duration_ms / 1000)} 秒"
                        f"（阈值 {ANDON_WAIT_THRESHOLD_MS // 1000} 秒）。"
                    ),
                    "at": _now_iso(),
                })
    return andons


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    """原子替换。

    ★ C 的契约要求「文件必须原子替换」：直接覆写时，
      桌面端可能读到写了一半的 JSON —— 它会判为坏文件并拒绝，
      于是**上一份一致快照也一起丢了**。
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)
