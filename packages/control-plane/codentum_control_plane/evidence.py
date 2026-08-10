"""证据引用的口径 —— 哪些证据能当验收依据。

════════════════════════════════════════════════════════════════
 为什么单独成一个模块
════════════════════════════════════════════════════════════════

这套判据原本只写在 `reconcile/loop.py` 里，于是**只有兜底分支用上了**：
`gates/builtin.py` 里的四个门禁各自写 `if not packet.evidence`，
把控制面自己写的 `sys:lock:` 簿记也算成了证据。

后果是反直觉的：**配了 `gate_runner` 的系统比没配的更松。**
`_try_review_to_accepted` 里门禁分支优先于兜底分支，而兜底分支在 08-09
已经修好（排除 `sys:` 前缀），门禁分支没有 —— 于是"打开护栏"这个动作
反而绕过了那次修复。

★ 判据放在共用模块里，是为了让「什么算证据」只有一个答案。
  同一个概念在两处各写一遍，迟早会在其中一处被修好、另一处留着。
  这次就是。

owner: A ｜ 相关：I6（状态推进必须附证据引用）
"""

from __future__ import annotations

from collections.abc import Iterable

__all__ = [
    "SYS_EVIDENCE_PREFIX",
    "WORKER_FAILED_EVIDENCE_PREFIX",
    "is_acceptance_evidence",
    "acceptance_evidence",
    "worker_failure_markers",
]

# ★ I6：「状态推进必须附证据引用，声明不算。执行完成但证据没落盘 = 没做过。」
#
# reconcile 自己在推进过程中会写入一些**簿记性**证据（拿到了锁、worker 失败了）。
# 这些是控制面的内部流水，不是「活干完了」的证明 —— 拿它们当验收依据，
# 等于系统自己给自己签字。所以统一加 `sys:` 前缀，验收时一律排除。
SYS_EVIDENCE_PREFIX = "sys:"

#: worker 以 failed 收场时打在 packet 上的标记，让失败对后续判定可见。
#: 没有它的话，失败只存在于 transition 的 detail 字符串里，验收环节读不到。
WORKER_FAILED_EVIDENCE_PREFIX = "sys:worker-failed:"


def is_acceptance_evidence(ref: str) -> bool:
    """这条证据能不能作为验收依据。控制面自产的簿记流水不算。"""
    return not ref.startswith(SYS_EVIDENCE_PREFIX)


def acceptance_evidence(refs: Iterable[str]) -> tuple[str, ...]:
    """筛出可作为验收依据的证据引用。"""
    return tuple(ref for ref in refs if is_acceptance_evidence(ref))


def worker_failure_markers(refs: Iterable[str]) -> tuple[str, ...]:
    """筛出 worker 失败标记。非空即表示这个 packet 有过明确失败。"""
    return tuple(
        ref for ref in refs if ref.startswith(WORKER_FAILED_EVIDENCE_PREFIX)
    )
