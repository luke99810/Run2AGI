"""判据缺口报告与资产负债表的判据。

★ 这两个脚本是用来找「没人守的东西」的 —— 它们自己没人守就太讽刺了。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from judgement_gaps import analyse  # noqa: E402
from judgement_ledger import _advise  # noqa: E402, PLC2701


# ══════════════════════════════════════════════════════════════
#  缺口判定（算子三）
# ══════════════════════════════════════════════════════════════


def test_recurring_failure_with_no_prior_judgement_is_a_gap() -> None:
    """★ 判据缺口的可观测定义：反复撞同一堵墙，而事前一条判据都没 fired 过。

    编码缺陷会让某条测试变红；判据缺陷不会 —— 因为缺的正是那条测试。
    这条信号是专门给它造的。
    """

    fingerprints = {"obs-a": [["p-1", "ref1"], ["p-2", "ref2"]]}
    gaps, guarded = analyse(fingerprints, fired=set(), threshold=2)

    assert "obs-a" in gaps
    assert not guarded


def test_one_guarded_packet_is_enough_to_disqualify_a_gap() -> None:
    """★ 缺口的定义是「一条判据都没有」，不是「没拦全」。

    只要有**一个** packet 在事前被判据 fired 过，说明系统里已经有东西
    在管这件事，只是那一次没拦住 —— 那是判据**强度**问题，
    与「压根没有判据」是两类完全不同的工作。
    混在一起报，会让人去补一条已经存在的规则。
    """

    fingerprints = {"obs-a": [["p-1", "ref1"], ["p-2", "ref2"]]}
    gaps, guarded = analyse(fingerprints, fired={"p-2"}, threshold=2)

    assert not gaps
    assert "obs-a" in guarded


def test_single_packet_recurrence_is_below_threshold() -> None:
    """★ 同一次执行里撞五遍是一条证据，不是五条 —— 与 L1 晋级同源的理由。

    账本按 (指纹, packet) 去重，所以这里的 threshold 数的是**不同 packet**。
    """

    fingerprints = {"obs-a": [["p-1", "ref1"]]}
    gaps, guarded = analyse(fingerprints, fired=set(), threshold=2)
    assert not gaps and not guarded


# ══════════════════════════════════════════════════════════════
#  资产负债表的建议逻辑
# ══════════════════════════════════════════════════════════════


def test_never_fired_and_unguarded_rule_is_flagged_for_deletion() -> None:
    """★ 这是这张表存在的主要理由。

    一条从没命中、也没有任何测试守着的判据，和没有这条判据
    在证据上不可区分 —— 但它会让判据集**看起来更长**，
    而那是一种廉价的安全感。
    """

    assert _advise("enforcing", fired=0, mutation="存活").startswith("★ 建议删除")


def test_never_fired_but_guarded_rule_is_kept() -> None:
    """有测试守着 = 它被改坏了会有信号。没命中只说明还没遇到坏情况。"""

    assert "留着" in _advise("enforcing", fired=0, mutation="被杀死")


def test_shadow_rule_needs_both_a_hit_and_a_guard_to_be_promotable() -> None:
    """★ 晋级两个条件缺一不可。

    只有命中没有守卫 → 它可能是对的，但改坏了没人知道
    只有守卫没有命中 → 从没命中过的判据，和没有这条判据在证据上不可区分
    """

    assert _advise("shadow", fired=3, mutation="被杀死").startswith("★ 够格晋级")
    assert "不可晋级" in _advise("shadow", fired=3, mutation="存活")
    assert "继续影子" in _advise("shadow", fired=0, mutation="被杀死")


def test_unobserved_is_not_reported_as_zero() -> None:
    """★ 「命中 0 次」与「没人在记录」含义相反，不能显示成同一个东西。

    0 次 → 判据可能多余
    未观测 → **观测本身坏了**，表上所有数字都不可信

    把后者显示成 0，等于用一个看起来正常的数字掩盖一个坏掉的管道。
    """

    assert _advise("enforcing", fired=None, mutation="存活") == "—（未观测）"
    assert "建议删除" not in _advise("enforcing", fired=None, mutation="存活")
