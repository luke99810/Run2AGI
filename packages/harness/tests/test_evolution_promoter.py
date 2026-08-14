"""L0 → L1 晋级的判据。

★ 这组守的是「凭什么升一级」。晋级门槛错了的后果是**静默**的：
  太松 → 一次性的偶然被当成普遍约束，喂给之后每一次执行；
  太紧 → 什么都晋不上去，而进化层看起来一切正常。
  两种都不会让任何别的测试变红 —— 只有这组会。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codentum_harness.evolution import Observation
from codentum_harness.evolution.promoter import FingerprintLedger, record_and_promote
from codentum_harness.memory_index import PersistentMemoryIndex

_AT = "2026-08-14T10:00:00Z"


def _obs(fingerprint: str, text: str, packet: str) -> Observation:
    return Observation(
        fingerprint=fingerprint,
        text=text,
        evidence_refs=(f"{packet}:tool_transcript.json#0",),
        tool="run_tests",
    )


def _fixture(tmp_path: Path) -> tuple[PersistentMemoryIndex, FingerprintLedger]:
    return (
        PersistentMemoryIndex(tmp_path / "index"),
        FingerprintLedger(tmp_path / "fingerprints.json"),
    )


def test_single_packet_does_not_promote(tmp_path: Path) -> None:
    """★ 撞一次不构成「重复出现」—— 那可能只是这一次的偶然。"""

    index, ledger = _fixture(tmp_path)
    promotions = record_and_promote(
        index, ledger, [_obs("obs-a", "ImportError", "p-1")],
        packet_id="p-1", role="coder", created_at=_AT,
    )
    assert promotions == []


def test_two_distinct_packets_promote_to_l1(tmp_path: Path) -> None:
    index, ledger = _fixture(tmp_path)
    record_and_promote(
        index, ledger, [_obs("obs-a", "ImportError", "p-1")],
        packet_id="p-1", role="coder", created_at=_AT,
    )
    promotions = record_and_promote(
        index, ledger, [_obs("obs-a", "ImportError", "p-2")],
        packet_id="p-2", role="coder", created_at=_AT,
    )

    assert len(promotions) == 1
    assert promotions[0].fingerprint == "obs-a"
    assert len(promotions[0].contributing_refs) == 2, "两条贡献的 L0 都要留在晋级理由里"


def test_same_packet_twice_never_promotes(tmp_path: Path) -> None:
    """★ 独立性是这条门槛的全部意义。

    一个卡在重试循环里的 packet 反复登记同一指纹，
    如果算数，它会单独把某条经验顶过晋级线 ——
    而那条经验很可能只是那次执行的挣扎，不是一条普遍约束。
    """

    index, ledger = _fixture(tmp_path)
    for _ in range(5):
        promotions = record_and_promote(
            index, ledger, [_obs("obs-a", "ImportError", "p-1")],
            packet_id="p-1", role="coder", created_at=_AT,
        )
        assert promotions == []


def test_promotion_is_not_repeated_on_third_packet(tmp_path: Path) -> None:
    """已经晋级过的指纹，第三个 packet 撞上不再产生新晋级。"""

    index, ledger = _fixture(tmp_path)
    rounds = [
        record_and_promote(
            index, ledger, [_obs("obs-a", "ImportError", packet)],
            packet_id=packet, role="coder", created_at=_AT,
        )
        for packet in ("p-1", "p-2", "p-3")
    ]
    assert [len(r) for r in rounds] == [0, 1, 0], "应当只在第二个 packet 时晋一次"


def test_promoted_entry_is_role_scoped_and_l1(tmp_path: Path) -> None:
    """★ 作用域的扩大**就是**晋级本身。

    L0 是 packet 作用域 —— 它此刻只是「这一次撞到的东西」，
    别的 packet 读不到它。升到 L1 同时升到 role 作用域，
    才谈得上「这个角色以后都该知道」。
    """

    index, ledger = _fixture(tmp_path)
    record_and_promote(index, ledger, [_obs("obs-a", "ImportError", "p-1")],
                       packet_id="p-1", role="coder", created_at=_AT)
    promotions = record_and_promote(index, ledger, [_obs("obs-a", "ImportError", "p-2")],
                                    packet_id="p-2", role="coder", created_at=_AT)

    entry = index._load_ref(promotions[0].ref)  # noqa: SLF001
    assert entry.level == "L1"
    assert entry.scope.kind == "role"
    assert entry.scope.role == "coder"


def test_ledger_survives_process_restart(tmp_path: Path) -> None:
    """★ 「重复出现」本来就要跨执行才成立。

    每个 packet 在自己的 worker 进程里跑完就退出 ——
    计数如果只活在内存里，它活不过一次执行，晋级永远触发不了，
    而且这个失败是**完全静默**的：进化层看起来在跑，只是「还没有够格的」。
    """

    index, ledger = _fixture(tmp_path)
    record_and_promote(index, ledger, [_obs("obs-a", "ImportError", "p-1")],
                       packet_id="p-1", role="coder", created_at=_AT)

    # 模拟进程重启：重新从磁盘读账本
    reborn = FingerprintLedger(tmp_path / "fingerprints.json")
    promotions = record_and_promote(index, reborn, [_obs("obs-a", "ImportError", "p-2")],
                                    packet_id="p-2", role="coder", created_at=_AT)
    assert len(promotions) == 1, "账本没跨进程存活，晋级永远触发不了"


def test_l1_promotion_goes_through_the_promote_door(tmp_path: Path) -> None:
    """★ 晋级必须走 promote()，不能直接写一条 level='L1' 的条目。

    直接写会绕开 promote() 里的全部校验 —— 单向性、以及
    「L3 必须附证伪门结论」那一条。给自己开一条不过门的旁路，
    等于那道门不存在。这里用 L3 反向验证那道门确实在生效。
    """

    from codentum_contracts.interfaces import PromotionJustification
    from codentum_harness.memory_index import MemoryIndexConflictError

    index, ledger = _fixture(tmp_path)
    record_and_promote(index, ledger, [_obs("obs-a", "ImportError", "p-1")],
                       packet_id="p-1", role="coder", created_at=_AT)
    promotions = record_and_promote(index, ledger, [_obs("obs-a", "ImportError", "p-2")],
                                    packet_id="p-2", role="coder", created_at=_AT)

    with pytest.raises(MemoryIndexConflictError, match="falsification_gate"):
        index.promote_now(
            promotions[0].ref, "L3",
            PromotionJustification(kind="observation", detail="想跳过证伪门", refs=[]),
        )
