"""WIP limits and scheduling projection.

WIP 满不是错误；它是看板在说「这列已经有足够多在制品」。这里的代码只做
确定性判断，把 packet 留在原状态等待下一轮，而不是把它标成 blocked。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal, cast

from codentum_contracts.state import PacketId, PacketState, WorkPacket

from .ready_queue import ReadyQueueEntry

ALL_PACKET_STATES: tuple[PacketState, ...] = (
    "pending",
    "ready",
    "running",
    "blocked",
    "review",
    "accepted",
    "rejected",
    "abandoned",
)

LIMITED_STATES: frozenset[PacketState] = frozenset({"running", "review"})
DEFAULT_WIP_LIMITS: Mapping[PacketState, int] = MappingProxyType(
    {
        "running": 4,
        "review": 2,
    }
)
SchedulingSource = Literal["default", "file"]


@dataclass(frozen=True, slots=True)
class SchedulingConfig:
    """Runtime scheduling policy loaded from `.codentum/scheduling.json`."""

    wip_limits: Mapping[PacketState, int]
    source: SchedulingSource = "default"

    def limit_for(self, state: PacketState) -> int | None:
        return self.wip_limits.get(state)


def default_scheduling_config() -> SchedulingConfig:
    return SchedulingConfig(
        wip_limits=MappingProxyType(dict(DEFAULT_WIP_LIMITS)),
        source="default",
    )


def load_scheduling_config(state_dir: str | Path) -> SchedulingConfig:
    """Load optional WIP config from `.codentum/scheduling.json`.

    The same file may also contain runtime projection fields written by the
    reconciler; only `schemaVersion` and `wipLimits` are configuration input.
    """

    path = Path(state_dir) / "scheduling.json"
    if not path.exists():
        return default_scheduling_config()

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"scheduling.json 不是合法 JSON：{exc}") from exc

    if not isinstance(raw, dict) or raw.get("schemaVersion") != 1:
        raise ValueError("scheduling.json 必须是 schemaVersion=1 的对象")

    raw_limits = raw.get("wipLimits", {})
    if not isinstance(raw_limits, dict):
        raise ValueError("scheduling.json 的 wipLimits 必须是对象")

    limits = dict(DEFAULT_WIP_LIMITS)
    for key, value in raw_limits.items():
        if key not in LIMITED_STATES:
            raise ValueError(f"scheduling.json 不支持 {key!r} 的 WIP 上限")
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"scheduling.json 的 {key!r} 上限必须是正整数")
        limits[cast(PacketState, key)] = value

    return SchedulingConfig(wip_limits=MappingProxyType(limits), source="file")


def count_packet_states(packets: Sequence[WorkPacket]) -> dict[PacketState, int]:
    counts = dict.fromkeys(ALL_PACKET_STATES, 0)
    for packet in packets:
        counts[packet.state] += 1
    return counts


def remaining_capacity(
    state: PacketState,
    counts: Mapping[PacketState, int],
    config: SchedulingConfig,
) -> int | None:
    limit = config.limit_for(state)
    if limit is None:
        return None
    return max(0, limit - counts.get(state, 0))


def under_wip_limit(
    state: PacketState,
    counts: Mapping[PacketState, int],
    config: SchedulingConfig,
) -> bool:
    capacity = remaining_capacity(state, counts, config)
    return capacity is None or capacity > 0


def build_scheduling_projection(
    *,
    packets: Sequence[WorkPacket],
    config: SchedulingConfig,
    ready_queue: Sequence[ReadyQueueEntry],
    selected_to_start: frozenset[PacketId],
) -> dict[str, object]:
    """Build a stable JSON projection for desktop and audits."""

    counts = count_packet_states(packets)
    limited_states = tuple(sorted(config.wip_limits, key=str))
    return {
        "schemaVersion": 1,
        "wipLimits": {str(state): config.wip_limits[state] for state in limited_states},
        "current": {str(state): counts[state] for state in ALL_PACKET_STATES},
        "capacity": {
            str(state): remaining_capacity(state, counts, config)
            for state in limited_states
        },
        "readyQueue": [
            {
                "rank": index + 1,
                "packetId": str(entry.packet_id),
                "criticalPath": entry.critical_path,
                "selectedToStart": entry.packet_id in selected_to_start,
            }
            for index, entry in enumerate(ready_queue)
        ],
        "policy": {
            "source": config.source,
            "ordering": "critical_path_desc_then_packet_id",
        },
    }
