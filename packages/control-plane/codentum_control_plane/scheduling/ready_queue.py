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
