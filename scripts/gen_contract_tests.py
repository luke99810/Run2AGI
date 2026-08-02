#!/usr/bin/env python
"""gen_contract_tests —— 从 schema + 固件生成契约测试

    packages/contracts/schemas/*.json + fixtures/golden-state/**
                        ↓
    tests/contract/test_*.py   （pytest）

════════════════════════════════════════════════════════════════
 这些测试测的是什么
════════════════════════════════════════════════════════════════

不是「我们的固件对不对」（那是 validate_fixtures 的事），
而是【任何实现都必须满足的契约行为】：

  正例：真实固件必须【通过】校验
  反例：删掉任一必填字段    → 必须【被拒】
        加一个未声明的字段  → 必须【被拒】
        枚举字段填非法值    → 必须【被拒】

★ 反例是从【真实固件变异】出来的，不是凭空造的。
  凭空造的实例很容易在别的地方就不合法了，于是测试通过的原因
  与它想测的东西无关 —— 这类"假绿灯"比没有测试更危险。

★ 同时用两条路径校验，两条都必须一致地拒绝：
    schema 层    scripts/lib/schema.py（零依赖，pip install 之前可跑）
    模型层       codentum_contracts 的 Pydantic 模型（运行时校验）
  两者不一致 = 生成器与 schema 之间出现了缝，而那道缝正是跨语言漂移的入口。

用法：
    python scripts/gen_contract_tests.py            生成
    python scripts/gen_contract_tests.py --check    校验一致性（CI 用）
"""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from lib.console import setup_console  # noqa: E402
from lib.schema import load_schemas, mutation_points  # noqa: E402

setup_console()

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT / "packages" / "contracts" / "schemas"
GOLDEN = ROOT / "fixtures" / "golden-state"
OUT_DIR = ROOT / "tests" / "contract"

schemas = load_schemas(SCHEMA_DIR)
snapshots = sorted(p.name for p in GOLDEN.iterdir() if p.is_dir())


def fail(msg: str) -> None:
    print(f"\n✗ gen-contract-tests 失败\n\n{msg}\n", file=sys.stderr)
    raise SystemExit(1)


def _read(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def collect_single(snap: str, fname: str) -> list[tuple[str, Any]]:
    p = GOLDEN / snap / ".codentum" / fname
    return [(f"{snap}/{fname}", _read(p))] if p.exists() else []


def collect_dir(snap: str, sub: str) -> list[tuple[str, Any]]:
    d = GOLDEN / snap / ".codentum" / sub
    return [(f"{snap}/{sub}/{p.name}", _read(p)) for p in sorted(d.glob("*.json"))] if d.is_dir() else []


def collect_lines(snap: str) -> list[tuple[str, Any]]:
    p = GOLDEN / snap / ".codentum" / "decisions.jsonl"
    if not p.exists():
        return []
    return [
        (f"{snap}/decisions.jsonl:{i}", json.loads(ln))
        for i, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if ln.strip()
    ]


# ★ 显式列出哪种固件对应哪份 schema / 哪个模型。
#   不做路径猜测 —— 猜错了会生成一堆测错东西的测试，且全绿。
TARGETS: list[dict[str, Any]] = [
    {"name": "workpacket", "model": "WorkPacket", "collect": lambda s: collect_dir(s, "packets")},
    {"name": "graph", "model": "GraphFile", "collect": lambda s: collect_single(s, "graph.json")},
    {"name": "budget", "model": "BudgetFile", "collect": lambda s: collect_single(s, "budget.json")},
    {"name": "evidence", "model": "Evidence", "collect": lambda s: collect_dir(s, "evidence")},
    {"name": "decision", "model": "DecisionRecord", "collect": collect_lines},
]

_DELETE = object()


def set_path(obj: Any, path: str, value: Any) -> Any | None:
    segs = [s for s in path.split(".") if s]
    copy = deepcopy(obj)
    cur = copy
    for s in segs[:-1]:
        if not isinstance(cur, dict) or s not in cur:
            return None
        cur = cur[s]
    last = segs[-1]
    if not isinstance(cur, dict) or last not in cur:
        return None
    if value is _DELETE:
        del cur[last]
    else:
        cur[last] = value
    return copy


def build_negatives(schema_file: str, sample: Any) -> list[tuple[str, Any]]:
    mp = mutation_points(schemas[schema_file], schema_file, schemas)
    cases: list[tuple[str, Any]] = []
    for path in mp["required"]:
        if (m := set_path(sample, path, _DELETE)) is not None:
            cases.append((f"删掉必填字段 {path[1:]}", m))
    for e in mp["enums"]:
        bad = "__不是合法枚举值__"
        if bad in e["values"]:
            continue
        if (m := set_path(sample, e["path"], bad)) is not None:
            cases.append((f'枚举字段 {e["path"][1:]} 填非法值', m))
    if "" in mp["closed"]:
        m = deepcopy(sample)
        m["__未声明的字段__"] = 1
        cases.append(("加一个未声明的字段（additionalProperties: false）", m))
    return cases


HEADER = '''"""契约测试：{name}

⚠️ 本文件由 `python scripts/gen_contract_tests.py` 生成。【不要手改】—— 下次生成会覆盖。

测的不是「我们的固件对不对」（那是 validate_fixtures 的事），
而是【任何实现都必须满足的契约行为】：合法的必须过，非法的必须拒。

★ 反例由真实固件变异而来，不是凭空造的 ——
  凭空造的实例容易在别处就不合法，于是测试通过的原因与它想测的东西无关。

★ 每条用例都走两条校验路径，两条都必须一致地判定：
    schema 层  scripts/lib/schema.py（零依赖）
    模型层     codentum_contracts.{model}（Pydantic 运行时校验）
  两者不一致 = 生成器与 schema 之间出现了缝，而那是跨语言漂移的入口。

跑：pytest tests/contract

真源：packages/contracts/schemas/{name}.schema.json
生成器：scripts/gen_contract_tests.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "packages" / "contracts" / "python"))

from codentum_contracts import {model}  # noqa: E402
from lib.schema import load_schemas, validate  # noqa: E402

SCHEMA_FILE = "{name}.schema.json"
_SCHEMAS = load_schemas(ROOT / "packages" / "contracts" / "schemas")


def check_schema(v: object) -> list[str]:
    return validate(_SCHEMAS[SCHEMA_FILE], v, _SCHEMAS, SCHEMA_FILE)


def check_model(v: object) -> list[str]:
    try:
        {model}.model_validate(v)
    except Exception as e:  # noqa: BLE001
        return [str(e)]
    return []
'''


def render(name: str, model: str, positives: list[tuple[str, Any]], negatives: list[tuple[str, Any]]) -> str:
    parts = [HEADER.format(name=name, model=model)]

    parts.append(
        f"\n# {'═' * 58}\n#  正例：{len(positives)} 份真实固件必须全部通过\n# {'═' * 58}\n\n"
        "POSITIVES = [\n"
        + "".join(f"    ({json.dumps(i, ensure_ascii=False)}, {json.dumps(v, ensure_ascii=False)}),\n" for i, v in positives)
        + "]\n\n\n"
        '@pytest.mark.parametrize(("fixture_id", "value"), POSITIVES, ids=[p[0] for p in POSITIVES])\n'
        "def test_positive(fixture_id: str, value: object) -> None:\n"
        "    assert check_schema(value) == [], f\"合法固件不应报错：{fixture_id}\"\n"
        "    assert check_model(value) == [], f\"Pydantic 模型拒绝了合法固件：{fixture_id}\"\n"
    )

    parts.append(
        f"\n# {'═' * 58}\n#  反例：{len(negatives)} 种变异必须【全部被拒】\n#\n"
        "#  ★ 这些不是\"边界情况\"，是契约的定义本身。\n"
        "#    任何一条变成绿灯，都说明校验器漏了一处，\n"
        "#    而漏掉的那处正是 Agent 最可能踩的地方。\n"
        f"# {'═' * 58}\n\n"
        "NEGATIVES = [\n"
        + "".join(f"    ({json.dumps(w, ensure_ascii=False)}, {json.dumps(v, ensure_ascii=False)}),\n" for w, v in negatives)
        + "]\n\n\n"
        '@pytest.mark.parametrize(("why", "value"), NEGATIVES, ids=[n[0] for n in NEGATIVES])\n'
        "def test_negative(why: str, value: object) -> None:\n"
        '    assert check_schema(value) != [], f"这份数据违反契约，schema 校验却放行了：{why}"\n'
        '    assert check_model(value) != [], f"这份数据违反契约，Pydantic 模型却放行了：{why}"\n'
    )

    return "".join(parts)


def main() -> None:
    files: dict[str, str] = {}
    for t in TARGETS:
        positives = [pv for s in snapshots for pv in t["collect"](s)]
        if not positives:
            fail(
                f"固件里找不到任何 {t['name']} 实例。\n\n"
                "★ 这是刻意报错。没有正例就没法变异出反例，生成一个空测试文件\n"
                "  等于给了一个永远绿的假信号 —— 比没有测试更危险。"
            )
        negatives = build_negatives(f"{t['name']}.schema.json", positives[0][1])
        files[f"test_{t['name']}.py"] = render(t["name"], t["model"], positives, negatives)

    if "--check" in sys.argv:
        bad: list[str] = []
        existing = sorted(p.name for p in OUT_DIR.glob("test_*.py")) if OUT_DIR.is_dir() else []
        bad += [f"多余：tests/contract/{f}" for f in existing if f not in files]
        for f, content in files.items():
            p = OUT_DIR / f
            if not p.exists() or p.read_text(encoding="utf-8") != content:
                bad.append(f"不一致：tests/contract/{f}")
        if bad:
            print("\n✗ tests/contract/ 与 schema/固件 不一致：\n  " + "\n  ".join(bad), file=sys.stderr)
            print("\n  跑 `python scripts/gen_contract_tests.py`。", file=sys.stderr)
            print("  ★ 不要手改 tests/contract/ —— 它是生成物。\n", file=sys.stderr)
            raise SystemExit(1)
        print(f"✓ tests/contract/ 与 schema 一致（{len(files)} 个文件）")
        return

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for p in OUT_DIR.glob("test_*.py"):
        if p.name not in files:
            p.unlink()
    total = 0
    for f, content in files.items():
        (OUT_DIR / f).write_text(content, encoding="utf-8")
        total += content.count("    (")
    print(f"✓ 已生成 tests/contract/（{len(files)} 个文件，{total} 个用例 ×2 条校验路径）")


if __name__ == "__main__":
    main()
