"""契约测试：graph

⚠️ 本文件由 `python scripts/gen_contract_tests.py` 生成。【不要手改】—— 下次生成会覆盖。

测的不是「我们的固件对不对」（那是 validate_fixtures 的事），
而是【任何实现都必须满足的契约行为】：合法的必须过，非法的必须拒。

★ 反例由真实固件变异而来，不是凭空造的 ——
  凭空造的实例容易在别处就不合法，于是测试通过的原因与它想测的东西无关。

★ 每条用例都走两条校验路径，两条都必须一致地判定：
    schema 层  scripts/lib/schema.py（零依赖）
    模型层     codentum_contracts.GraphFile（Pydantic 运行时校验）
  两者不一致 = 生成器与 schema 之间出现了缝，而那是跨语言漂移的入口。

跑：pytest tests/contract

真源：packages/contracts/schemas/graph.schema.json
生成器：scripts/gen_contract_tests.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "packages" / "contracts" / "python"))

from codentum_contracts import GraphFile  # noqa: E402
from lib.schema import load_schemas, validate  # noqa: E402

SCHEMA_FILE = "graph.schema.json"
_SCHEMAS = load_schemas(ROOT / "packages" / "contracts" / "schemas")


def check_schema(v: object) -> list[str]:
    return validate(_SCHEMAS[SCHEMA_FILE], v, _SCHEMAS, SCHEMA_FILE)


def check_model(v: object) -> list[str]:
    try:
        GraphFile.model_validate(v)
    except Exception as e:  # noqa: BLE001
        return [str(e)]
    return []

# ══════════════════════════════════════════════════════════
#  正例：3 份真实固件必须全部通过
# ══════════════════════════════════════════════════════════

POSITIVES = [
    ("blocked/graph.json", {"schemaVersion": 1, "dependency": {"nodes": ["wp-a1c001", "wp-b2d002", "wp-c3e003", "wp-d4f004", "wp-f6h006"], "edges": [{"from": "wp-a1c001", "to": "wp-b2d002"}, {"from": "wp-a1c001", "to": "wp-c3e003"}, {"from": "wp-a1c001", "to": "wp-d4f004"}, {"from": "wp-b2d002", "to": "wp-f6h006"}]}, "ownership": {"locks": [{"pathPrefix": "src/api/", "heldBy": "wp-c3e003", "acquiredAt": "2026-08-02T10:41:12.000Z"}, {"pathPrefix": "src/web/", "heldBy": "wp-d4f004", "acquiredAt": "2026-08-02T10:38:55.000Z"}], "version": 23}}),
    ("empty/graph.json", {"schemaVersion": 1, "dependency": {"nodes": [], "edges": []}, "ownership": {"locks": [], "version": 0}}),
    ("mid-flight/graph.json", {"schemaVersion": 1, "dependency": {"nodes": ["wp-a1c001", "wp-b2d002", "wp-c3e003", "wp-d4f004", "wp-e5g005"], "edges": [{"from": "wp-a1c001", "to": "wp-b2d002"}, {"from": "wp-a1c001", "to": "wp-c3e003"}, {"from": "wp-a1c001", "to": "wp-d4f004"}, {"from": "wp-b2d002", "to": "wp-e5g005"}, {"from": "wp-c3e003", "to": "wp-e5g005"}, {"from": "wp-d4f004", "to": "wp-e5g005"}]}, "ownership": {"locks": [{"pathPrefix": "src/auth/", "heldBy": "wp-b2d002", "acquiredAt": "2026-08-02T09:14:03.000Z"}, {"pathPrefix": "src/api/", "heldBy": "wp-c3e003", "acquiredAt": "2026-08-02T09:14:07.000Z"}, {"pathPrefix": "src/web/", "heldBy": "wp-d4f004", "acquiredAt": "2026-08-02T09:15:41.000Z"}], "version": 7}}),
]


@pytest.mark.parametrize(("fixture_id", "value"), POSITIVES, ids=[p[0] for p in POSITIVES])
def test_positive(fixture_id: str, value: object) -> None:
    assert check_schema(value) == [], f"合法固件不应报错：{fixture_id}"
    assert check_model(value) == [], f"Pydantic 模型拒绝了合法固件：{fixture_id}"

# ══════════════════════════════════════════════════════════
#  反例：8 种变异必须【全部被拒】
#
#  ★ 这些不是"边界情况"，是契约的定义本身。
#    任何一条变成绿灯，都说明校验器漏了一处，
#    而漏掉的那处正是 Agent 最可能踩的地方。
# ══════════════════════════════════════════════════════════

NEGATIVES = [
    ("删掉必填字段 schemaVersion", {"dependency": {"nodes": ["wp-a1c001", "wp-b2d002", "wp-c3e003", "wp-d4f004", "wp-f6h006"], "edges": [{"from": "wp-a1c001", "to": "wp-b2d002"}, {"from": "wp-a1c001", "to": "wp-c3e003"}, {"from": "wp-a1c001", "to": "wp-d4f004"}, {"from": "wp-b2d002", "to": "wp-f6h006"}]}, "ownership": {"locks": [{"pathPrefix": "src/api/", "heldBy": "wp-c3e003", "acquiredAt": "2026-08-02T10:41:12.000Z"}, {"pathPrefix": "src/web/", "heldBy": "wp-d4f004", "acquiredAt": "2026-08-02T10:38:55.000Z"}], "version": 23}}),
    ("删掉必填字段 dependency", {"schemaVersion": 1, "ownership": {"locks": [{"pathPrefix": "src/api/", "heldBy": "wp-c3e003", "acquiredAt": "2026-08-02T10:41:12.000Z"}, {"pathPrefix": "src/web/", "heldBy": "wp-d4f004", "acquiredAt": "2026-08-02T10:38:55.000Z"}], "version": 23}}),
    ("删掉必填字段 ownership", {"schemaVersion": 1, "dependency": {"nodes": ["wp-a1c001", "wp-b2d002", "wp-c3e003", "wp-d4f004", "wp-f6h006"], "edges": [{"from": "wp-a1c001", "to": "wp-b2d002"}, {"from": "wp-a1c001", "to": "wp-c3e003"}, {"from": "wp-a1c001", "to": "wp-d4f004"}, {"from": "wp-b2d002", "to": "wp-f6h006"}]}}),
    ("删掉必填字段 dependency.nodes", {"schemaVersion": 1, "dependency": {"edges": [{"from": "wp-a1c001", "to": "wp-b2d002"}, {"from": "wp-a1c001", "to": "wp-c3e003"}, {"from": "wp-a1c001", "to": "wp-d4f004"}, {"from": "wp-b2d002", "to": "wp-f6h006"}]}, "ownership": {"locks": [{"pathPrefix": "src/api/", "heldBy": "wp-c3e003", "acquiredAt": "2026-08-02T10:41:12.000Z"}, {"pathPrefix": "src/web/", "heldBy": "wp-d4f004", "acquiredAt": "2026-08-02T10:38:55.000Z"}], "version": 23}}),
    ("删掉必填字段 dependency.edges", {"schemaVersion": 1, "dependency": {"nodes": ["wp-a1c001", "wp-b2d002", "wp-c3e003", "wp-d4f004", "wp-f6h006"]}, "ownership": {"locks": [{"pathPrefix": "src/api/", "heldBy": "wp-c3e003", "acquiredAt": "2026-08-02T10:41:12.000Z"}, {"pathPrefix": "src/web/", "heldBy": "wp-d4f004", "acquiredAt": "2026-08-02T10:38:55.000Z"}], "version": 23}}),
    ("删掉必填字段 ownership.locks", {"schemaVersion": 1, "dependency": {"nodes": ["wp-a1c001", "wp-b2d002", "wp-c3e003", "wp-d4f004", "wp-f6h006"], "edges": [{"from": "wp-a1c001", "to": "wp-b2d002"}, {"from": "wp-a1c001", "to": "wp-c3e003"}, {"from": "wp-a1c001", "to": "wp-d4f004"}, {"from": "wp-b2d002", "to": "wp-f6h006"}]}, "ownership": {"version": 23}}),
    ("删掉必填字段 ownership.version", {"schemaVersion": 1, "dependency": {"nodes": ["wp-a1c001", "wp-b2d002", "wp-c3e003", "wp-d4f004", "wp-f6h006"], "edges": [{"from": "wp-a1c001", "to": "wp-b2d002"}, {"from": "wp-a1c001", "to": "wp-c3e003"}, {"from": "wp-a1c001", "to": "wp-d4f004"}, {"from": "wp-b2d002", "to": "wp-f6h006"}]}, "ownership": {"locks": [{"pathPrefix": "src/api/", "heldBy": "wp-c3e003", "acquiredAt": "2026-08-02T10:41:12.000Z"}, {"pathPrefix": "src/web/", "heldBy": "wp-d4f004", "acquiredAt": "2026-08-02T10:38:55.000Z"}]}}),
    ("加一个未声明的字段（additionalProperties: false）", {"schemaVersion": 1, "dependency": {"nodes": ["wp-a1c001", "wp-b2d002", "wp-c3e003", "wp-d4f004", "wp-f6h006"], "edges": [{"from": "wp-a1c001", "to": "wp-b2d002"}, {"from": "wp-a1c001", "to": "wp-c3e003"}, {"from": "wp-a1c001", "to": "wp-d4f004"}, {"from": "wp-b2d002", "to": "wp-f6h006"}]}, "ownership": {"locks": [{"pathPrefix": "src/api/", "heldBy": "wp-c3e003", "acquiredAt": "2026-08-02T10:41:12.000Z"}, {"pathPrefix": "src/web/", "heldBy": "wp-d4f004", "acquiredAt": "2026-08-02T10:38:55.000Z"}], "version": 23}, "__未声明的字段__": 1}),
]


@pytest.mark.parametrize(("why", "value"), NEGATIVES, ids=[n[0] for n in NEGATIVES])
def test_negative(why: str, value: object) -> None:
    assert check_schema(value) != [], f"这份数据违反契约，schema 校验却放行了：{why}"
    assert check_model(value) != [], f"这份数据违反契约，Pydantic 模型却放行了：{why}"
