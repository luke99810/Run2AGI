"""守住护栏消融实验本身 —— 不让它退化成一张好看但空转的图。

════════════════════════════════════════════════════════════════
 为什么实验也需要测试
════════════════════════════════════════════════════════════════

这份实验要产出的是**参赛材料里的那张对照数据图**。
一张图只要还能画出来，没有人会发现它已经不测量任何东西了 ——
柱子照样有高度，颜色照样对，只是数字不再对应任何事实。

项目在 §十五 / §十七 反复写过同一条判据：

    「写完一条测试，先问：如果这个功能坏了，它会不会红？」

那么对实验本身也要问一遍：**如果实验坏了，哪条测试会变红？**
就是这一份。它守三件事：

1. 护栏开的那一侧必须**全拦住**（leaked == 0）
   —— 否则我们在拿一张"护栏也没用"的图去证明护栏有用。
2. 护栏关的那一侧必须**真的漏**（leaked > 0）
   —— 否则两侧一样高，图上没有任何信息，而它看起来仍然是一张图。
3. 并发对照组不能退化成「根本没有锁」
   —— 那样每轮必然 5 个赢家，等于把结论直接写进对照组。
      这一条是本文件里最容易被无声破坏的：把 `_UnsynchronizedLockTable`
      换回 `_NoLockTable`，上面两条**依然全绿**。

★ 第 3 条正是 §十五 那个教训的复现：对照组配错了，实验照样出数。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from experiment_guardrails import (  # noqa: E402
    collect,
    measure_budget,
    measure_gate_fix,
    measure_guardian,
    measure_lock,
    measure_lock_race,
    render_svg,
)


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".codentum"
    (d / "packets").mkdir(parents=True)
    return d


# ════════════════════════════════════════════════════════════════
#  1) 护栏开 —— 必须全拦住
# ════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("measure", [measure_lock, measure_guardian, measure_budget])
def test_guardrail_on_leaks_nothing(measure, state_dir: Path) -> None:
    r = measure(state_dir)
    assert r["leaked_on"] == 0, (
        f"{r['invariant']}：护栏开着还漏了 {r['leaked_on']}/{r['injected']} 个"
        f"（故障：{r['fault']}；违规表现：{r['violation']}）"
    )


def test_gate_fix_leaks_nothing_after_fix() -> None:
    r = measure_gate_fix()
    assert r["leaked_after_fix"] == 0, (
        f"修复后的门禁仍放行 {r['leaked_after_fix']}/{r['injected']} 例"
    )


# ════════════════════════════════════════════════════════════════
#  2) 护栏关 —— 必须真的漏，否则这张图不测量任何东西
# ════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("measure", [measure_lock, measure_guardian, measure_budget])
def test_guardrail_off_actually_leaks(measure, state_dir: Path) -> None:
    r = measure(state_dir)
    assert r["leaked_off"] > 0, (
        f"{r['invariant']}：护栏关掉后一个都没漏 —— "
        f"要么故障没注进去，要么这条不变量根本不由该组件强制。"
        f"两种情况下这组数据都不能拿来证明护栏有用。"
    )
    assert r["leaked_off"] > r["leaked_on"], (
        f"{r['invariant']}：开与关的结果没有差别，这一组是空转的"
    )


def test_gate_fix_had_something_to_fix() -> None:
    """修复前必须真的漏 —— 否则「修了一个 bug」这个说法本身没有证据。"""
    r = measure_gate_fix()
    assert r["leaked_before_fix"] > 0, (
        "修复前的门禁一例都没放行 —— 那这次修复没有对应任何真实缺陷"
    )


# ════════════════════════════════════════════════════════════════
#  3) 并发对照组不许退化成「根本没有锁」
# ════════════════════════════════════════════════════════════════

def test_race_control_group_is_not_a_strawman() -> None:
    """对照组要保留冲突检测、只去掉互斥锁。

    ★ 判据：平均赢家数必须**严格小于线程数**。
      等于线程数 = 每个线程都赢 = 判定逻辑被整个摘掉了，
      那测的就不是「并发下判定还成不成立」，而是「没有判定会怎样」。
    """
    r = measure_lock_race(rounds=60, threads=5)
    assert 1.0 < r["avg_winners_off"] < r["threads"], (
        f"对照组平均赢家 {r['avg_winners_off']}（线程 {r['threads']}）—— "
        f"落在 1 说明竞争没发生（switchinterval 没压下去？），"
        f"落在 {r['threads']} 说明对照组变成了「根本没有锁」"
    )
    assert r["leaked_on"] == 0, "有锁时出现了多赢家，I1 被破坏"
    assert r["leaked_off"] > 0, "无互斥锁时一次冲突都没有 —— 这个压测是空转的"


# ════════════════════════════════════════════════════════════════
#  4) 产物本身
# ════════════════════════════════════════════════════════════════

def test_collect_and_render(state_dir: Path) -> None:
    data = collect(state_dir)
    svg = render_svg(data)
    assert svg.startswith("<svg"), "SVG 头部不对"
    assert svg.rstrip().endswith("</svg>")
    # 图里必须出现每一组的实际数字，避免"图画出来了但没接上数据"
    for g in data["ablation"]:
        assert f'{g["leaked_off"]}/{g["injected"]}' in svg, (
            f"{g['invariant']} 的数字没有出现在图里"
        )
    json.dumps(data, ensure_ascii=False)  # 必须可序列化
