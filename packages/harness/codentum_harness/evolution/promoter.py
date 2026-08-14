"""L0 → L1 晋级：把「撞过一次」变成「反复撞」。

════════════════════════════════════════════════════════════════
 ★ 为什么 L1 的门槛是「跨 ≥2 个不同 packet」
════════════════════════════════════════════════════════════════

契约里 L1 的定义就是「重复出现」。但「重复」有两种，只有一种算数：

| | 例子 | 算不算 |
|---|---|---|
| 同一次执行里撞 5 次 | 模型陷在重试循环，同一堵墙撞五遍 | ❌ 1 条证据 |
| 两个 packet 各撞 1 次 | 两个互不知情的执行者撞上同一个约束 | ✅ 2 条证据 |

区别在于**独立性**。前者是一个执行者的一次挣扎，后者是同一个约束
在不同语境下各自现身 —— 只有后者能支持「这是一条普遍约束」的推断。

★ 不做这个区分的后果是具体的：一个卡在重试循环里的 packet
  会单独把某条经验顶过晋级线，而那条经验很可能只是**那次执行的偶然**。
  进化层会因此学到一堆一次性的东西，并且以 L1 的身份喂给之后每一次执行。

════════════════════════════════════════════════════════════════
 ★ 为什么用旁挂账本记指纹，而不给 MemoryEntry 加字段
════════════════════════════════════════════════════════════════

`MemoryEntry` 是冻结契约（I3）。为了一个实现层的聚类需要去改契约，
代价是所有实现方跟着动 —— 而指纹是**这一层怎么归类**的问题，
不是记忆条目本身的属性。旁挂账本坏了只影响晋级，改契约坏了影响所有人。

════════════════════════════════════════════════════════════════
 ★ 晋级到 L1 的 justification 必须指回贡献它的那几条 L0
════════════════════════════════════════════════════════════════

否则事后没法回答「凭什么它是 L1」。而这个问题一定会被问 ——
当某条 L1 经验把执行者带偏时，第一件事就是去查它是怎么上来的。
查不出来的晋级链等于没有晋级链。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from codentum_contracts import MemoryEntry, PacketId
from codentum_contracts.interfaces import MemoryScope, PromotionJustification
from codentum_contracts.state import RoleId

from codentum_harness.memory_index import PersistentMemoryIndex

from .observations import Observation

__all__ = ["Promotion", "FingerprintLedger", "record_and_promote"]

MIN_DISTINCT_PACKETS = 2
"""晋级到 L1 所需的**不同 packet** 数。"""


@dataclass(frozen=True, slots=True)
class Promotion:
    ref: str
    fingerprint: str
    contributing_refs: tuple[str, ...]


class FingerprintLedger:
    """指纹 → 贡献过它的 (packet_id, l0_ref) 列表。

    ★ 用 JSON 文件而不是内存字典：晋级是**跨进程、跨天**的判断 ——
      每个 packet 在自己的 worker 进程里跑完就退出了，
      内存里的计数活不过一次执行，而「重复出现」本来就要跨执行才成立。
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._data: dict[str, list[list[str]]] = {}
        if path.exists():
            self._data = json.loads(path.read_text(encoding="utf-8"))

    def record(self, fingerprint: str, packet_id: str, ref: str) -> tuple[str, ...]:
        """登记一次出现，返回该指纹目前来自的**不同 packet** 的 L0 refs。"""

        rows = self._data.setdefault(fingerprint, [])
        # ★ 同一个 packet 再次登记同一指纹不增加计数 ——
        #   独立性是这条门槛的全部意义，重复登记会把它架空。
        if not any(row[0] == packet_id for row in rows):
            rows.append([packet_id, ref])
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._data, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
        return tuple(row[1] for row in rows)

    def distinct_packets(self, fingerprint: str) -> int:
        return len(self._data.get(fingerprint, []))


def record_and_promote(
    index: PersistentMemoryIndex,
    ledger: FingerprintLedger,
    observations: list[Observation],
    *,
    packet_id: str,
    role: RoleId,
    created_at: str,
) -> list[Promotion]:
    """写入本次执行的 L0，并把够格的指纹晋级为 L1。

    返回本次产生的晋级。★ 返回而不是只记日志 —— 调用方要能把
    「这次执行让系统学到了什么」作为**可观测的产出**报出去，
    否则进化层跑没跑过，从外面看不出区别。
    """

    promotions: list[Promotion] = []

    for obs in observations:
        l0_ref = index.write_now(
            MemoryEntry(
                ref="",
                level="L0",
                # ★ L0 的作用域是 packet：它此刻只是「这一次撞到的东西」，
                #   还没有资格被别的 packet 读到。作用域的扩大就是晋级本身。
                scope=MemoryScope(kind="packet", role=role, packet_id=PacketId(packet_id)),
                text=obs.text,
                created_at=created_at,
            )
        )
        contributing = ledger.record(obs.fingerprint, packet_id, l0_ref)

        if len(contributing) < MIN_DISTINCT_PACKETS:
            continue
        if ledger.distinct_packets(obs.fingerprint) > MIN_DISTINCT_PACKETS:
            continue  # 已经晋级过了，不重复晋级

        # ★ 先按 L0 写一条 role 作用域的条目，再 promote 上去 ——
        #   而不是直接写一条 level="L1"。
        #   直接写会绕开 promote() 里的全部校验（单向性、L3 证伪门），
        #   等于给自己开了一条不过门的旁路。晋级必须走晋级的那道门。
        role_ref = index.write_now(
            MemoryEntry(
                ref="",
                level="L0",
                scope=MemoryScope(kind="role", role=role),
                text=obs.text,
                created_at=created_at,
            )
        )
        index.promote_now(
            role_ref,
            "L1",
            PromotionJustification(
                kind="observation",
                detail=(
                    f"同一失败指纹 {obs.fingerprint} 在 "
                    f"{len(contributing)} 个不同 packet 中复现。"
                ),
                refs=list(contributing),
            ),
        )
        promotions.append(
            Promotion(ref=role_ref, fingerprint=obs.fingerprint, contributing_refs=contributing)
        )

    return promotions
