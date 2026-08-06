"""契约测试：budget

⚠️ 本文件由 `python scripts/gen_contract_tests.py` 生成。【不要手改】—— 下次生成会覆盖。

测的不是「我们的固件对不对」（那是 validate_fixtures 的事），
而是【任何实现都必须满足的契约行为】：合法的必须过，非法的必须拒。

★ 反例由真实固件变异而来，不是凭空造的 ——
  凭空造的实例容易在别处就不合法，于是测试通过的原因与它想测的东西无关。

★ 每条用例都走两条校验路径，两条都必须一致地判定：
    schema 层  scripts/lib/schema.py（零依赖）
    模型层     codentum_contracts.BudgetFile（Pydantic 运行时校验）
  两者不一致 = 生成器与 schema 之间出现了缝，而那是跨语言漂移的入口。

跑：pytest tests/contract

真源：packages/contracts/schemas/budget.schema.json
生成器：scripts/gen_contract_tests.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "packages" / "contracts" / "python"))

from codentum_contracts import BudgetFile  # noqa: E402
from lib.schema import load_schemas, validate  # noqa: E402

SCHEMA_FILE = "budget.schema.json"
_SCHEMAS = load_schemas(ROOT / "packages" / "contracts" / "schemas")


def check_schema(v: object) -> list[str]:
    return validate(_SCHEMAS[SCHEMA_FILE], v, _SCHEMAS, SCHEMA_FILE)


def check_model(v: object) -> list[str]:
    try:
        BudgetFile.model_validate(v)
    except Exception as e:  # noqa: BLE001
        return [str(e)]
    return []

# ══════════════════════════════════════════════════════════
#  正例：3 份真实固件必须全部通过
# ══════════════════════════════════════════════════════════

POSITIVES = [
    ("blocked/budget.json", {"schemaVersion": 1, "currency": "CNY", "limitCny": 20, "spentCny": 18.4, "byRole": {"architect": 0.91, "planner": 0.34, "qa": 1.12, "coder": 12.86, "reviewer": 2.05, "helper": 1.12}, "byModel": {"claude-opus-5": 14.23, "claude-sonnet-5": 4.17}, "degradationChain": ["drop_semantic_memory", "shrink_review_context", "summarize_history"], "alerts": [{"level": "warn", "at": "2026-08-02T10:44:02.000Z", "message": "已用 92% 预算（18.40 / 20.00 USD）。剩余 1.60 USD，约够 1 个 impl packet。"}]}),
    ("empty/budget.json", {"schemaVersion": 1, "currency": "CNY", "limitCny": 20, "spentCny": 0, "byRole": {}, "byModel": {}, "degradationChain": ["drop_semantic_memory", "shrink_review_context", "summarize_history"], "alerts": []}),
    ("mid-flight/budget.json", {"schemaVersion": 1, "currency": "CNY", "limitCny": 20, "spentCny": 4.82, "byRole": {"architect": 0.91, "qa": 0.63, "coder": 2.74, "reviewer": 0.44, "planner": 0.1}, "byModel": {"claude-opus-5": 3.65, "claude-sonnet-5": 1.17}, "degradationChain": ["drop_semantic_memory", "shrink_review_context", "summarize_history"], "alerts": []}),
]


@pytest.mark.parametrize(("fixture_id", "value"), POSITIVES, ids=[p[0] for p in POSITIVES])
def test_positive(fixture_id: str, value: object) -> None:
    assert check_schema(value) == [], f"合法固件不应报错：{fixture_id}"
    assert check_model(value) == [], f"Pydantic 模型拒绝了合法固件：{fixture_id}"

# ══════════════════════════════════════════════════════════
#  反例：5 种变异必须【全部被拒】
#
#  ★ 这些不是"边界情况"，是契约的定义本身。
#    任何一条变成绿灯，都说明校验器漏了一处，
#    而漏掉的那处正是 Agent 最可能踩的地方。
# ══════════════════════════════════════════════════════════

NEGATIVES = [
    ("删掉必填字段 schemaVersion", {"currency": "CNY", "limitCny": 20, "spentCny": 18.4, "byRole": {"architect": 0.91, "planner": 0.34, "qa": 1.12, "coder": 12.86, "reviewer": 2.05, "helper": 1.12}, "byModel": {"claude-opus-5": 14.23, "claude-sonnet-5": 4.17}, "degradationChain": ["drop_semantic_memory", "shrink_review_context", "summarize_history"], "alerts": [{"level": "warn", "at": "2026-08-02T10:44:02.000Z", "message": "已用 92% 预算（18.40 / 20.00 USD）。剩余 1.60 USD，约够 1 个 impl packet。"}]}),
    ("删掉必填字段 currency", {"schemaVersion": 1, "limitCny": 20, "spentCny": 18.4, "byRole": {"architect": 0.91, "planner": 0.34, "qa": 1.12, "coder": 12.86, "reviewer": 2.05, "helper": 1.12}, "byModel": {"claude-opus-5": 14.23, "claude-sonnet-5": 4.17}, "degradationChain": ["drop_semantic_memory", "shrink_review_context", "summarize_history"], "alerts": [{"level": "warn", "at": "2026-08-02T10:44:02.000Z", "message": "已用 92% 预算（18.40 / 20.00 USD）。剩余 1.60 USD，约够 1 个 impl packet。"}]}),
    ("删掉必填字段 limitCny", {"schemaVersion": 1, "currency": "CNY", "spentCny": 18.4, "byRole": {"architect": 0.91, "planner": 0.34, "qa": 1.12, "coder": 12.86, "reviewer": 2.05, "helper": 1.12}, "byModel": {"claude-opus-5": 14.23, "claude-sonnet-5": 4.17}, "degradationChain": ["drop_semantic_memory", "shrink_review_context", "summarize_history"], "alerts": [{"level": "warn", "at": "2026-08-02T10:44:02.000Z", "message": "已用 92% 预算（18.40 / 20.00 USD）。剩余 1.60 USD，约够 1 个 impl packet。"}]}),
    ("删掉必填字段 spentCny", {"schemaVersion": 1, "currency": "CNY", "limitCny": 20, "byRole": {"architect": 0.91, "planner": 0.34, "qa": 1.12, "coder": 12.86, "reviewer": 2.05, "helper": 1.12}, "byModel": {"claude-opus-5": 14.23, "claude-sonnet-5": 4.17}, "degradationChain": ["drop_semantic_memory", "shrink_review_context", "summarize_history"], "alerts": [{"level": "warn", "at": "2026-08-02T10:44:02.000Z", "message": "已用 92% 预算（18.40 / 20.00 USD）。剩余 1.60 USD，约够 1 个 impl packet。"}]}),
    ("加一个未声明的字段（additionalProperties: false）", {"schemaVersion": 1, "currency": "CNY", "limitCny": 20, "spentCny": 18.4, "byRole": {"architect": 0.91, "planner": 0.34, "qa": 1.12, "coder": 12.86, "reviewer": 2.05, "helper": 1.12}, "byModel": {"claude-opus-5": 14.23, "claude-sonnet-5": 4.17}, "degradationChain": ["drop_semantic_memory", "shrink_review_context", "summarize_history"], "alerts": [{"level": "warn", "at": "2026-08-02T10:44:02.000Z", "message": "已用 92% 预算（18.40 / 20.00 USD）。剩余 1.60 USD，约够 1 个 impl packet。"}], "__未声明的字段__": 1}),
]


@pytest.mark.parametrize(("why", "value"), NEGATIVES, ids=[n[0] for n in NEGATIVES])
def test_negative(why: str, value: object) -> None:
    assert check_schema(value) != [], f"这份数据违反契约，schema 校验却放行了：{why}"
    assert check_model(value) != [], f"这份数据违反契约，Pydantic 模型却放行了：{why}"
