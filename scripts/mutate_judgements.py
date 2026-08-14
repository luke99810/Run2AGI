"""算子一：判据的因果检验（元变异）。

════════════════════════════════════════════════════════════════
 ★ 这是 vacuity_check 抬高一层
════════════════════════════════════════════════════════════════

`acceptance.vacuity_check` 做的是：把**用户的实现**移走，验收测试必须变红。
它防的是「执行者交上来的验收测试是空的」。

但那个算子有一个它自己管不到的盲区 —— **它自己是谁在守？**
更一般地：控制平面那 8 条准入规则、4 道门禁，**有没有任何测试在守着它们？**

一条判据被摘掉、全套测试依旧全绿 → 这条判据**无人看守**。
它可能昨天就已经被谁改坏了，而没有任何信号。

★ 与常规变异测试的区别：
  常规变异测试「变异实现，检验测试写得够不够」。
  这里是「**变异判据，检验判据自己有没有被覆盖**」。
  在 Agent 系统里这个区别是要害 —— 判据层是模型和用户之间唯一的东西，
  判据层无人看守，整套安全叙事就是装饰。

════════════════════════════════════════════════════════════════
 ★ 这个脚本自己的判据（三个控制点，缺一不可）
════════════════════════════════════════════════════════════════

1. **基线必须绿** —— 不变异时套件本来就是红的话，
   后面每一个「被杀死」都可能是那条既有的红，与变异无关。
2. **至少一条被杀死** —— 证明变异真的注入进去了。
   插件没加载时每轮跑的都是未变异代码，结论会是「全部存活」。
3. **正对照必须存活**（canary：塞一条什么都不管的规则）——
   证明这个脚本**有能力报出「存活」**。
   一个只会输出同一个答案的检查，和不做检查是等价的。

★ 少了第 3 点，「8 条全部被杀死」这个漂亮结论是不可信的 ——
  它和「杀死判定永远为真」在证据上不可区分。

════════════════════════════════════════════════════════════════
 ★ 两级跑法
════════════════════════════════════════════════════════════════

全量套件一轮约 140 秒，12 条判据全跑全量要半小时。
所以：先跑控制平面那组（快），被杀死就收工；**只有存活的才升级到全量确认**。
理由是不对称的 —— 「被杀死」一条反例就成立，
而「存活」是全称命题，必须跑遍所有测试才敢下结论。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "scripts" / "lib"

FAST_TIER = ["packages/control-plane/tests"]
"""快筛层：准入与门禁的直接测试都在这里。"""


@dataclass(frozen=True, slots=True)
class Outcome:
    target: str
    killed: bool
    tier: str
    seconds: float
    detail: str


def _run_pytest(mutant: str, paths: list[str], *, timeout: int = 900) -> tuple[bool, str]:
    """返回 (套件是否全绿, 摘要)。"""

    env = dict(os.environ)
    env["CODENTUM_MUTANT"] = mutant
    env["PYTHONPATH"] = str(LIB) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-x", "-q", "-p", "mutation_plugin", *paths],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    tail = (proc.stdout or "").strip().splitlines()
    summary = tail[-1] if tail else f"(无输出, exit={proc.returncode})"
    return proc.returncode == 0, summary


def _discover() -> tuple[list[str], list[str]]:
    """列出全部可变异的判据。★ 硬编码名单会漂移 —— 新加的判据必须自动进入检验。"""

    # ★ 复用根 conftest 的路径装配，而不是在这里再抄一份 _IMPORT_ROOTS ——
    #   抄一份就有两处要同步维护，新增一个包时这里会静默地少一条路径。
    sys.path.insert(0, str(REPO))
    import conftest  # noqa: F401  （导入即完成 sys.path 装配）

    from codentum_control_plane.admission.rules import DEFAULT_RULES
    from codentum_control_plane.gates.builtin import register_builtin_gates

    rules = [fn.__name__ for fn in DEFAULT_RULES]

    collected: list[str] = []

    class _Probe:
        def register(self, gate_id: str, _fn: object) -> None:
            collected.append(gate_id)

    register_builtin_gates(_Probe())  # type: ignore[arg-type]
    return rules, collected


_FULL_BASELINE_CHECKED: list[bool] = []


def _ensure_full_baseline() -> bool:
    """全量基线 —— 最多付一次，且只在真的需要判「存活」时才付。

    ★ 「被杀死」一条反例就成立，快筛能杀死的根本用不着全量；
      而「存活」是全称命题，必须跑遍所有测试才敢下结论。
      两者代价不对称，跑法就不该对称。
    """

    if _FULL_BASELINE_CHECKED:
        return _FULL_BASELINE_CHECKED[0]

    print(f"{'[全量基线]':<38}", end="", flush=True)
    t0 = time.monotonic()
    green, summary = _run_pytest("none", [])
    print(f"{'绿 ✅' if green else '红 ❌':<12}{time.monotonic() - t0:.0f}s  {summary}")
    if not green:
        print("\n★ 全量基线是红的 —— 快筛层的结论仍然有效（那一层基线是绿的），")
        print("  但「存活」判断做不了：全量本来就红，变异后还是红，区分不出来。")
    _FULL_BASELINE_CHECKED.append(green)
    return green


def _two_pass(targets: list[tuple[str, str]], *, phase: str) -> list[Outcome] | None:
    """对一批变异体跑「先快筛、存疑者升全量」。targets 是 (变异标识, 显示名)。"""

    print(f"\n{'═' * 68}\n{phase} · {len(targets)} 个变异体\n{'═' * 68}")
    print(f"{'变异体':<38}{'结论':<12}{'耗时'}")
    print("─" * 68)

    results: list[Outcome] = []
    pending: list[tuple[str, str]] = []
    for spec, label in targets:
        t0 = time.monotonic()
        fast_green, summary = _run_pytest(spec, FAST_TIER)
        elapsed = time.monotonic() - t0
        if fast_green:
            pending.append((spec, label))
            print(f"{label:<38}{'快筛未杀死 →':<12}{elapsed:.0f}s", flush=True)
        else:
            results.append(Outcome(spec, True, "快筛", elapsed, summary))
            print(f"{label:<38}{'被杀死 ✅':<12}{elapsed:.0f}s", flush=True)

    if pending:
        print(f"\n升级全量（{len(pending)} 个快筛未杀死）")
        print("─" * 68)
        if not _ensure_full_baseline():
            return None
        for spec, label in pending:
            t0 = time.monotonic()
            green_after, summary = _run_pytest(spec, [])
            elapsed = time.monotonic() - t0
            killed = not green_after
            results.append(Outcome(spec, killed, "全量", elapsed, summary))
            print(f"{label:<38}{'被杀死 ✅' if killed else '存活 ⚠️':<12}{elapsed:.0f}s", flush=True)

    return results


def _weak_targets() -> list[tuple[str, str]]:
    """枚举全部弱变异点。

    ★ 逐个点名而不是「每条判据随机改一处」：变异测试的结论要可复现，
      随机采样会让今天的 92% 和明天的 85% 无法比较 ——
      而这个数存在的意义就是**被跨时间比较**。
    """

    sys.path.insert(0, str(LIB))
    from weak_mutations import enumerate_sites  # type: ignore[import-not-found]

    from codentum_control_plane.admission.rules import DEFAULT_RULES
    from codentum_control_plane.gates import builtin as gates_mod

    functions = [*DEFAULT_RULES, *(
        getattr(gates_mod, name)
        for name in ("evidence_exists_gate", "self_test_gate", "acceptance_gate", "review_gate")
    )]

    from equivalent_mutants import KNOWN_EQUIVALENT  # type: ignore[import-not-found]

    targets: list[tuple[str, str]] = []
    for fn in functions:
        for site in enumerate_sites(fn):
            key = f"{fn.__name__}:{site.index}"
            # ★ 已判定为等价的**不跑** —— 排除的依据是读代码的论证，
            #   不是跑出来的。花 18 分钟去确认一个「行为上不可观测」的改动
            #   仍然不可观测，是在给一个已经成立的结论买保险。
            #   代价是这条不再有实证背书 —— 所以理由必须写进
            #   equivalent_mutants.py，并且每次报告都打印出来。
            if key in KNOWN_EQUIVALENT:
                continue
            targets.append((f"weak:{key}", f"{fn.__name__} [{site.detail}]"))
    return targets


def _report(results: list[Outcome], *, phase: str, meaning: str, exclude: bool = False) -> None:
    excluded: dict[str, str] = {}
    if exclude:
        sys.path.insert(0, str(LIB))
        from equivalent_mutants import KNOWN_EQUIVALENT

        excluded = KNOWN_EQUIVALENT
        # ★ 整张表每次都打印，不管有没有命中 —— 隐藏的排除项等于没有排除项。
        #   看报告的人必须能看见「这个数是在排除了这些之后算出来的」。
        print(f"\n已判定为等价变异体（{len(excluded)} 条 · 已排除，未运行）：")
        for key, reason in excluded.items():
            print(f"    · {key}\n      {reason}")

    counted = [r for r in results if r.target.removeprefix("weak:") not in excluded]
    survived = [r for r in counted if not r.killed]
    rate = len(survived) / len(counted) * 100 if counted else 0.0

    print(f"\n{phase}存活率：{len(survived)}/{len(counted)} = {rate:.0f}%")
    print(f"（存活 = {meaning}）")
    if survived:
        print("\n⚠️  存活的变异体 —— 需要人看一眼：")
        for r in survived:
            print(f"    · {r.target}")


def main() -> int:
    mode = "both"
    for arg in sys.argv[1:]:
        if arg.startswith("--mode="):
            mode = arg.removeprefix("--mode=")
    if mode not in {"strong", "weak", "both"}:
        print("用法：mutate_judgements.py [--mode=strong|weak|both]")
        return 2

    rules, gates = _discover()

    print("═" * 68)
    print(f" 判据因果检验 · {len(rules)} 条准入规则 + {len(gates)} 道门禁")
    print("═" * 68)

    # ── 控制点 1：基线 ─────────────────────────────────────────
    print("\n[控制点 1/3] 基线（不变异，快筛层）…", end=" ", flush=True)
    t0 = time.monotonic()
    green, summary = _run_pytest("none", FAST_TIER)
    print(f"{'绿 ✅' if green else '红 ❌'}  ({time.monotonic() - t0:.0f}s)  {summary}")
    if not green:
        print("\n★ 基线是红的 —— 后面每一个「被杀死」都可能是这条既有的红，")
        print("  与变异无关。先修好基线再跑本脚本。")
        return 2

    # ── 控制点 3：正对照 ───────────────────────────────────────
    print("[控制点 3/3] 正对照 canary（塞一条无所谓的规则，应存活）…", end=" ", flush=True)
    t0 = time.monotonic()
    canary_green, _ = _run_pytest("canary", FAST_TIER)
    print(f"{'存活 ✅' if canary_green else '被杀死 ❌'}  ({time.monotonic() - t0:.0f}s)")
    if not canary_green:
        print("\n★ 正对照被杀死了 —— 说明「往规则集里加一条空规则」本身就能让测试变红，")
        print("  多半是有测试在断言规则的**数量或名字列表**。")
        print("  那类结构断言会污染本脚本的全部结论，必须先排除。")
        return 2

    all_results: list[Outcome] = []

    if mode in {"strong", "both"}:
        targets = [(f"rule:{r}", r) for r in rules] + [(f"gate:{g}", g) for g in gates]
        strong = _two_pass(targets, phase="强变异（整条判据摘掉）")
        if strong is None:
            return 2
        all_results += strong
        _report(strong, phase="强变异", meaning="摘掉它也没有任何测试变红 = 这条判据无人看守")

    if mode in {"weak", "both"}:
        weak = _two_pass(_weak_targets(), phase="弱变异（边界挪一格）")
        if weak is None:
            return 2
        all_results += weak
        _report(
            weak,
            phase="弱变异",
            meaning="边界挪了一格也没人发现 = 这一格没被测过",
            exclude=True,
        )
        print("\n★ 弱变异的存活者是「**需要人看一眼**」，不是确定的缺陷 ——")
        print("  有些变异在语义上与原代码等价（改的是走不到的分支），必然存活。")
        print("  这是变异测试的固有噪声，把它算成缺陷和算成通过是同一种不诚实。")

    # ── 控制点 2 ───────────────────────────────────────────────
    killed_n = sum(1 for r in all_results if r.killed)
    print(f"\n{'─' * 68}")
    print(f"[控制点 2/3] 至少一条被杀死：{'✅' if killed_n else '❌'}  ({killed_n}/{len(all_results)})")
    if not killed_n:
        print("\n★ 一条都没被杀死 —— 先怀疑管道而不是判据集。")
        print("  最可能的原因是插件没被加载，每轮跑的都是未变异的代码。")
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
