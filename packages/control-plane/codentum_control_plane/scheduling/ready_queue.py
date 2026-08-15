"""Ready queue construction.

The queue is the pull signal for workers: only packets that are ready,
dependency-satisfied, lock-compatible, and highest on the remaining critical
path should be admitted into `running` when WIP capacity exists.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from codentum_contracts.state import PacketId, PacketState, PathLock, WorkPacket
from codentum_control_plane.locks import LockTable, normalize_path


@dataclass(frozen=True, slots=True)
class ReadyQueueEntry:
    packet_id: PacketId
    critical_path: int


def build_ready_queue(
    packets: Mapping[PacketId, WorkPacket],
    *,
    lock_table: LockTable,
    dep_states: Mapping[PacketId, PacketState],
) -> tuple[ReadyQueueEntry, ...]:
    lengths = _critical_path_lengths(packets)
    locks = lock_table.to_ownership().locks
    entries = [
        ReadyQueueEntry(packet_id=packet.id, critical_path=lengths.get(packet.id, 1))
        for packet in packets.values()
        if packet.state == "ready"
        and _deps_satisfied(packet, dep_states)
        and not _has_lock_conflict(packet, locks)
    ]
    return tuple(
        sorted(
            entries,
            key=lambda entry: (-entry.critical_path, str(entry.packet_id)),
        )
    )


def critical_path_chain(packets: Mapping[PacketId, WorkPacket]) -> tuple[str, ...]:
    """未完成 packet 中最长的一条依赖链（packet id 序列）。

    ★ 与 `ReadyQueueEntry.critical_path` 不是一回事：那个是**剩余链长**（整数），
      用于排序；这个是**链本身**，桌面端契约里的 `criticalPath` 要的是它。
      两者同源但用途不同，混用会让界面显示一个数字而不是一条路径。

    ★ 由控制平面算而不是让桌面端算：桌面端手上只有当前快照，
      两边各算一遍必然在某个时刻不一致 —— **而不一致时没有任何东西会报错**。
    """

    live = {
        pid: pkt for pid, pkt in packets.items()
        if pkt.state not in {"accepted", "abandoned", "rejected"}
    }
    memo: dict[PacketId, tuple[str, ...]] = {}

    def chain(pid: PacketId, seen: frozenset[PacketId]) -> tuple[str, ...]:
        if pid in memo:
            return memo[pid]
        if pid in seen or pid not in live:
            return ()
        best: tuple[str, ...] = ()
        for dep in live[pid].deps:
            candidate = chain(dep, seen | {pid})
            if len(candidate) > len(best):
                best = candidate
        result = (*best, str(pid))
        memo[pid] = result
        return result

    longest: tuple[str, ...] = ()
    for pid in sorted(live):
        candidate = chain(pid, frozenset())
        if len(candidate) > len(longest):
            longest = candidate
    return longest


def _deps_satisfied(
    packet: WorkPacket,
    dep_states: Mapping[PacketId, PacketState],
) -> bool:
    return all(dep_states.get(dep_id) == "accepted" for dep_id in packet.deps)


def _critical_path_lengths(
    packets: Mapping[PacketId, WorkPacket],
) -> dict[PacketId, int]:
    children: dict[PacketId, list[PacketId]] = defaultdict(list)
    for packet in packets.values():
        for dep_id in packet.deps:
            if dep_id in packets:
                children[dep_id].append(packet.id)

    memo: dict[PacketId, int] = {}
    visiting: set[PacketId] = set()

    def walk(packet_id: PacketId) -> int:
        cached = memo.get(packet_id)
        if cached is not None:
            return cached
        if packet_id in visiting:
            return 1

        visiting.add(packet_id)
        downstream = children.get(packet_id, ())
        length = 1 + max((walk(child) for child in downstream), default=0)
        visiting.remove(packet_id)
        memo[packet_id] = length
        return length

    return {packet_id: walk(packet_id) for packet_id in packets}


def _has_lock_conflict(packet: WorkPacket, locks: Sequence[PathLock]) -> bool:
    if not packet.ownsPaths:
        return False
    try:
        requested = [_path_key(path) for path in packet.ownsPaths]
    except ValueError:
        return True

    for index, left in enumerate(requested):
        for right in requested[index + 1 :]:
            if _overlaps(left, right):
                return True

    for lock in locks:
        if lock.heldBy == packet.id:
            continue
        try:
            held = _path_key(lock.pathPrefix)
        except ValueError:
            return True
        if any(_overlaps(path, held) for path in requested):
            return True
    return False


def _path_key(path: str) -> tuple[str, ...]:
    return tuple(segment.casefold() for segment in normalize_path(path).split("/"))


def _overlaps(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    return (
        left == right
        or left[: len(right)] == right
        or right[: len(left)] == left
    )
