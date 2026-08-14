"""判据资产负债表 —— 把判据当生产资产来看。

════════════════════════════════════════════════════════════════
 ★ 为什么需要一张表，而不是三个各自打印的脚本
════════════════════════════════════════════════════════════════

现在关于判据的信息散在三个地方：

  · 档位（shadow / enforcing）  → 代码里的 JUDGEMENT_MODES
  · 变异检验（被杀死 / 存活）    → mutate_judgements.py 跑完就打印在终端里
  · 生命周期命中次数            → .codentum/judgements/hits.jsonl

单看任何一个都得不出结论。**要合起来看才有意义**：

  enforcing + 从未命中 + 变异存活   → 它既没在生产拦过东西，
                                      也没有任何测试守着 → **该删**
  enforcing + 从未命中 + 变异被杀死 → 留着（有测试守，只是还没遇到坏情况）
  shadow    + 命中过   + 变异被杀死 → **够格晋级 enforcing**
  shadow    + 从未命中               → 继续影子 —— 还没有证据

★ 「该删」这一条是这张表存在的主要理由。一条从没命中、也没人守的判据，
  和没有这条判据，在证据上不可区分 —— 但它会让判据集**看起来更长**，
  而那是一种廉价的安全感。

════════════════════════════════════════════════════════════════
 ★ 「命中 0 次」与「没人在记录」必须分开显示
════════════════════════════════════════════════════════════════

两者在账本上都是「没有这条判据的行」，但含义完全相反：

  命中 0 次   → 判据可能多余
  没人在记录  → **观测本身坏了**，这张表上的所有数字都不可信

把后者显示成 0，等于用一个看起来正常的数字掩盖一个坏掉的管道。
所以没有账本时显示 `未观测`，而不是 `0`。
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


@dataclass(frozen=True, slots=True)
class Row:
    name: str
    kind: str
    mode: str
    runs: int | None
    """None = 没有任何记录（未观测），与 0 次严格区分。"""
    fired: int | None
    last_fired: str
    mutation: str
    advice: str


def _load_hits(ledger: Path) -> tuple[Counter[str], Counter[str], dict[str, str]] | None:
    if not ledger.exists():
        return None
    runs: Counter[str] = Counter()
    fired: Counter[str] = Counter()
    last: dict[str, str] = {}
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        rule = str(row.get("rule", ""))
        runs[rule] += 1
        if row.get("fired"):
            fired[rule] += 1
            last[rule] = str(row.get("at", ""))
    return runs, fired, last


def _load_mutation(path: Path) -> dict[str, bool]:
    """判据名 → 是否**每一个**变异体都被杀死。

    ★ 只要有一个变异体存活，这条判据就算「有未被守住的边界」——
      按最坏情况报，不按平均。平均会把一个真实缺口稀释掉。
    """

    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    verdict: dict[str, bool] = {}
    for item in payload.get("results", []):
        target = str(item["target"])
        # rule:check_x / gate:acceptance / weak:check_x:3
        parts = target.split(":")
        name = parts[1] if len(parts) >= 2 else target
        verdict[name] = verdict.get(name, True) and bool(item["killed"])
    return verdict


def _advise(mode: str, fired: int | None, mutation: str) -> str:
    if fired is None:
        return "—（未观测）"
    if mode == "shadow":
        if fired == 0:
            return "继续影子：还没有命中过，晋级无据"
        if mutation == "存活":
            return "不可晋级：没有测试守着它"
        if mutation == "未检验":
            return "先跑变异检验再谈晋级"
        return "★ 够格晋级 enforcing"
    if fired == 0 and mutation == "存活":
        return "★ 建议删除：既没拦过东西，也没人守着"
    if fired == 0:
        return "留着：有测试守，只是还没遇到坏情况"
    return "正常"


def main() -> int:
    state_dir = REPO / ".codentum"
    mutation_path = REPO / ".codentum" / "judgements" / "mutation.json"
    for arg in sys.argv[1:]:
        if arg.startswith("--state-dir="):
            state_dir = Path(arg.removeprefix("--state-dir="))
        elif arg.startswith("--mutation="):
            mutation_path = Path(arg.removeprefix("--mutation="))

    sys.path.insert(0, str(REPO))
    import conftest  # noqa: F401

    from codentum_control_plane.admission.rules import DEFAULT_RULES, JUDGEMENT_MODES
    from codentum_control_plane.gates.builtin import register_builtin_gates

    gate_ids: list[str] = []

    class _Probe:
        def register(self, gate_id: str, _fn: object) -> None:
            gate_ids.append(gate_id)

    register_builtin_gates(_Probe())  # type: ignore[arg-type]

    hits = _load_hits(state_dir / "judgements" / "hits.jsonl")
    mutation = _load_mutation(mutation_path)

    rows: list[Row] = []
    for fn in DEFAULT_RULES:
        name = fn.__name__
        mode = JUDGEMENT_MODES.get(name, "enforcing")
        runs = hits[0][name] if hits else None
        fired = hits[1][name] if hits else None
        last = hits[2].get(name, "—") if hits else "—"
        verdict = "未检验" if name not in mutation else ("被杀死" if mutation[name] else "存活")
        rows.append(Row(name, "规则", mode, runs, fired, last, verdict, _advise(mode, fired, verdict)))

    for gate_id in gate_ids:
        verdict = "未检验" if gate_id not in mutation else ("被杀死" if mutation[gate_id] else "存活")
        # ★ 门禁目前没有命中记录 —— recorder 只挂在 AdmissionChecker 上。
        #   如实显示「未观测」，不填 0。
        rows.append(Row(gate_id, "门禁", "enforcing", None, None, "—", verdict, "—（门禁未接命中记录）"))

    print("═" * 100)
    print(" 判据资产负债表")
    print("═" * 100)
    if hits is None:
        print("\n⚠️  没有找到命中账本（%s）。" % (state_dir / "judgements" / "hits.jsonl"))
        print("    「命中」各列显示为**未观测**而不是 0 —— 两者含义相反：")
        print("    0 次说明判据可能多余；未观测说明**观测本身坏了**，表上的数字都不可信。")
    if not mutation:
        print(f"\n⚠️  没有找到变异结果（{mutation_path}）。先跑：")
        print("    python scripts/mutate_judgements.py --mode=both")

    print(f"\n{'判据':<36}{'类型':<6}{'档位':<11}{'跑过':<7}{'命中':<7}{'变异检验':<11}{'建议'}")
    print("─" * 100)
    for row in rows:
        runs_cell = "未观测" if row.runs is None else str(row.runs)
        fired_cell = "未观测" if row.fired is None else str(row.fired)
        print(
            f"{row.name:<36}{row.kind:<6}{row.mode:<11}"
            f"{runs_cell:<7}{fired_cell:<7}{row.mutation:<11}{row.advice}"
        )
    print("─" * 100)

    actionable = [r for r in rows if r.advice.startswith("★")]
    if actionable:
        print("\n需要处置：")
        for row in actionable:
            print(f"    · {row.name} —— {row.advice.removeprefix('★ ')}")
    else:
        print("\n没有需要处置的判据。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
