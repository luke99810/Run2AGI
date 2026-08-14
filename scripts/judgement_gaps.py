"""算子三：判据缺口报告 —— 反复发生、却从没有任何判据拦过的失败。

════════════════════════════════════════════════════════════════
 ★ 「判据缺陷」的可观测定义
════════════════════════════════════════════════════════════════

这个项目一路在拆的核心问题是**判据缺陷**：

    编码缺陷会让某条测试变红。
    判据缺陷不会让任何测试变红 —— 因为缺的正是那条测试。

所以判据缺陷抓不到，不是因为它隐蔽，是因为**它没有信号**。

这个脚本给它造一个信号：

    同一类失败在 ≥N 个不同 packet 里复现，
    而这些 packet 在准入时**一条判据都没 fired 过**。

反复撞同一堵墙、而事前没有任何东西拦过 —— 那就是一个判据缺口的位置。

════════════════════════════════════════════════════════════════
 ★ 为什么只报缺口，不自动写规则
════════════════════════════════════════════════════════════════

自动生成判据是很自然的下一步，而且技术上不难。**但它是错的。**

判据是用来判执行者的。让系统根据自己的失败去写判据，
等于**执行者给自己出考题** —— 这直接违反本项目自己定的
`mustDifferFrom`（qa / reviewer 不得与 coder 同模型）。

自进化系统里，哪一环必须留给外部，是**设计问题**不是能力问题。
这里的边界划在：**检测自动化，补规则留给人或独立角色。**

★ 补上的规则应当先进 shadow 档位（见 rules.JUDGEMENT_MODES），
  攒够命中证据再晋级 —— 那条链路已经在了。

════════════════════════════════════════════════════════════════
 ★ 数据量不够时会是空的，而那**不代表没有缺口**
════════════════════════════════════════════════════════════════

聚类需要样本。累计运行只有几十次量级时，N=2 都很难触发。

空结果必须报成「**样本不足，未能判定**」而不是「没有缺口」——
后者是一个零输入的绿灯，而那正是本项目反复在拆的东西。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

MIN_DISTINCT_PACKETS = 2
"""构成「反复发生」所需的**不同 packet** 数。与 promoter.MIN_DISTINCT_PACKETS 同源理由：
同一次执行里撞五遍是一条证据，不是五条。"""


def _fired_packets(ledger: Path) -> set[str] | None:
    """返回「准入时至少有一条判据 fired 过」的 packet 集合。None = 没有账本。"""

    if not ledger.exists():
        return None
    fired: set[str] = set()
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("fired"):
            fired.add(str(row.get("packet", "")))
    return fired


def _memory_texts(index_dir: Path) -> dict[str, str]:
    """L0 ref → 正文，用来把指纹翻译成人看得懂的失败描述。"""

    texts: dict[str, str] = {}
    entries = index_dir / "entries"
    if not entries.is_dir():
        return texts
    for path in sorted(entries.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        texts[str(payload.get("ref", ""))] = str(payload.get("text", ""))
    return texts


def analyse(
    fingerprints: dict[str, list[list[str]]],
    fired: set[str],
    threshold: int,
) -> tuple[dict[str, list[list[str]]], dict[str, list[list[str]]]]:
    """返回 (缺口, 已被守住的)。

    ★ 抽成纯函数是为了让它**可以被测试**。埋在 main() 里的判定逻辑
      只能靠肉眼看输出确认，而这个脚本本身就是用来找「没人守的东西」的 ——
      它自己没人守就太讽刺了。
    """

    gaps: dict[str, list[list[str]]] = {}
    guarded: dict[str, list[list[str]]] = {}
    for fingerprint, rows in fingerprints.items():
        packets = {row[0] for row in rows}
        if len(packets) < threshold:
            continue
        # ★ 只要有**一个** packet 在事前被判据 fired 过，这类失败就不算缺口 ——
        #   说明系统里已经有东西在管它，只是那一次没拦住。
        #   缺口的定义是「一条判据都没有」，不是「没拦全」。
        (guarded if packets & fired else gaps)[fingerprint] = rows
    return gaps, guarded


def main() -> int:
    state_dir = REPO / ".codentum"
    threshold = MIN_DISTINCT_PACKETS
    for arg in sys.argv[1:]:
        if arg.startswith("--state-dir="):
            state_dir = Path(arg.removeprefix("--state-dir="))
        elif arg.startswith("--min-packets="):
            threshold = int(arg.removeprefix("--min-packets="))

    fingerprints_path = state_dir / "memory" / "fingerprints.json"
    hits_path = state_dir / "judgements" / "hits.jsonl"

    print("═" * 76)
    print(" 判据缺口报告")
    print("═" * 76)

    if not fingerprints_path.exists():
        print(f"\n⚠️  没有找到失败指纹账本：{fingerprints_path}")
        print("    进化层还没有沉淀过任何 L0 观察 —— **样本不足，未能判定**。")
        print("    这不等于「没有缺口」。")
        return 0

    fingerprints: dict[str, list[list[str]]] = json.loads(
        fingerprints_path.read_text(encoding="utf-8")
    )
    fired = _fired_packets(hits_path)
    texts = _memory_texts(state_dir / "memory" / "index")

    if fired is None:
        print(f"\n⚠️  没有找到判据命中账本：{hits_path}")
        print("    无法回答「失败之前有没有判据拦过」—— **这个报告做不了**。")
        print("    ★ 硬报「全是缺口」比报空更糟：那会把一堆本来有人守的失败")
        print("      算成缺口，而修它们是纯粹的浪费。")
        return 0

    gaps, guarded = analyse(fingerprints, fired, threshold)
    recurring = {**gaps, **guarded}

    print(f"\n指纹总数 {len(fingerprints)} · 达到 ≥{threshold} 个不同 packet 的 {len(recurring)}")

    if not recurring:
        print("\n**样本不足，未能判定。**")
        print(f"    没有任何失败在 ≥{threshold} 个不同 packet 里复现过。")
        print("    ★ 这不等于「没有缺口」—— 聚类需要样本，而累计运行还太少。")
        print("      把它读成绿灯，就是又一个零输入的绿灯。")
        return 0

    if guarded:
        print(f"\n✅ {len(guarded)} 类反复失败在事前**有判据 fired 过** —— 不是缺口。")

    if not gaps:
        print("\n没有发现判据缺口。")
        return 0

    print(f"\n⚠️  {len(gaps)} 类反复发生的失败，事前**一条判据都没有 fired 过**：\n")
    for fp, rows in sorted(gaps.items()):
        involved = sorted({row[0] for row in rows})
        sample = next((texts.get(row[1], "") for row in rows if texts.get(row[1])), "")
        print(f"  [{fp}] 出现在 {len(involved)} 个 packet：{', '.join(involved[:4])}")
        if sample:
            print(f"      {sample[:150]}")
        print()

    print("★ 这是**缺口提案**，不是自动修复。")
    print("  补规则留给人或独立角色 —— 让系统根据自己的失败去写判据，")
    print("  等于执行者给自己出考题，直接违反 mustDifferFrom。")
    print("  补上的规则应先进 shadow 档位，攒够命中证据再晋级。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
