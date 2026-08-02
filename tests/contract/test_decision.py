"""契约测试：decision

⚠️ 本文件由 `python scripts/gen_contract_tests.py` 生成。【不要手改】—— 下次生成会覆盖。

测的不是「我们的固件对不对」（那是 validate_fixtures 的事），
而是【任何实现都必须满足的契约行为】：合法的必须过，非法的必须拒。

★ 反例由真实固件变异而来，不是凭空造的 ——
  凭空造的实例容易在别处就不合法，于是测试通过的原因与它想测的东西无关。

★ 每条用例都走两条校验路径，两条都必须一致地判定：
    schema 层  scripts/lib/schema.py（零依赖）
    模型层     codentum_contracts.DecisionRecord（Pydantic 运行时校验）
  两者不一致 = 生成器与 schema 之间出现了缝，而那是跨语言漂移的入口。

跑：pytest tests/contract

真源：packages/contracts/schemas/decision.schema.json
生成器：scripts/gen_contract_tests.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "packages" / "contracts" / "python"))

from codentum_contracts import DecisionRecord  # noqa: E402
from lib.schema import load_schemas, validate  # noqa: E402

SCHEMA_FILE = "decision.schema.json"
_SCHEMAS = load_schemas(ROOT / "packages" / "contracts" / "schemas")


def check_schema(v: object) -> list[str]:
    return validate(_SCHEMAS[SCHEMA_FILE], v, _SCHEMAS, SCHEMA_FILE)


def check_model(v: object) -> list[str]:
    try:
        DecisionRecord.model_validate(v)
    except Exception as e:  # noqa: BLE001
        return [str(e)]
    return []

# ══════════════════════════════════════════════════════════
#  正例：22 份真实固件必须全部通过
# ══════════════════════════════════════════════════════════

POSITIVES = [
    ("blocked/decisions.jsonl:1", {"at": "2026-08-02T09:11:44.000Z", "actor": "integrator", "action": "packet_accepted", "packetId": "wp-a1c001", "reasonCode": "gate_passed", "detail": "acceptance"}),
    ("blocked/decisions.jsonl:2", {"at": "2026-08-02T09:52:10.000Z", "actor": "coder", "action": "attempt_failed", "packetId": "wp-b2d002", "reasonCode": "acceptance_not_met", "detail": "attempt 1/3 —— tests/auth 4 项未过"}),
    ("blocked/decisions.jsonl:3", {"at": "2026-08-02T09:52:12.000Z", "actor": "manager", "action": "escalation", "packetId": "wp-b2d002", "reasonCode": "L0_self_repair", "detail": "允许自修复重试"}),
    ("blocked/decisions.jsonl:4", {"at": "2026-08-02T10:14:33.000Z", "actor": "coder", "action": "attempt_failed", "packetId": "wp-b2d002", "reasonCode": "acceptance_not_met", "detail": "attempt 2/3 —— 同一处 session 过期逻辑仍未过"}),
    ("blocked/decisions.jsonl:5", {"at": "2026-08-02T10:14:35.000Z", "actor": "manager", "action": "escalation", "packetId": "wp-b2d002", "reasonCode": "L0.5_peer_debug", "detail": "同类失败重复出现，转 Peer-Debug"}),
    ("blocked/decisions.jsonl:6", {"at": "2026-08-02T10:40:58.000Z", "actor": "coder", "action": "attempt_failed", "packetId": "wp-b2d002", "reasonCode": "budget_exhausted", "detail": "attempt 3/3 —— packet 预算 2.50 USD 用尽"}),
    ("blocked/decisions.jsonl:7", {"at": "2026-08-02T10:41:00.000Z", "actor": "manager", "action": "lock_released", "packetId": "wp-b2d002", "reasonCode": "blocked_release", "detail": "src/auth/ —— 卡住即释放锁，不占用下游"}),
    ("blocked/decisions.jsonl:8", {"at": "2026-08-02T10:41:02.000Z", "actor": "manager", "action": "escalation", "packetId": "wp-b2d002", "reasonCode": "L1_helper", "detail": "转 Helper 介入诊断"}),
    ("blocked/decisions.jsonl:9", {"at": "2026-08-02T10:41:12.000Z", "actor": "manager", "action": "lock_acquired", "packetId": "wp-c3e003", "reasonCode": "path_free", "detail": "src/api/"}),
    ("blocked/decisions.jsonl:10", {"at": "2026-08-02T10:44:02.000Z", "actor": "manager", "action": "budget_alert", "reasonCode": "warn_92pct", "detail": "18.40 / 20.00 USD"}),
    ("blocked/decisions.jsonl:11", {"at": "2026-08-02T10:47:30.000Z", "actor": "helper", "action": "packet_created", "packetId": "wp-f6h006", "reasonCode": "diagnosis_complete", "detail": "定位到 session 过期判定，产出 fix packet"}),
    ("blocked/decisions.jsonl:12", {"at": "2026-08-02T10:47:31.000Z", "actor": "guardian", "action": "approval_required", "packetId": "wp-f6h006", "reasonCode": "contract_adjacent", "detail": "改动紧邻契约边界，需人工审批"}),
    ("mid-flight/decisions.jsonl:1", {"at": "2026-08-02T09:02:00.000Z", "actor": "planner", "action": "packet_created", "packetId": "wp-a1c001", "reasonCode": "plan_initial"}),
    ("mid-flight/decisions.jsonl:2", {"at": "2026-08-02T09:11:44.000Z", "actor": "integrator", "action": "packet_accepted", "packetId": "wp-a1c001", "reasonCode": "gate_passed", "detail": "acceptance"}),
    ("mid-flight/decisions.jsonl:3", {"at": "2026-08-02T09:12:00.000Z", "actor": "planner", "action": "packet_created", "packetId": "wp-b2d002", "reasonCode": "contract_frozen_fanout"}),
    ("mid-flight/decisions.jsonl:4", {"at": "2026-08-02T09:12:00.000Z", "actor": "planner", "action": "packet_created", "packetId": "wp-c3e003", "reasonCode": "contract_frozen_fanout"}),
    ("mid-flight/decisions.jsonl:5", {"at": "2026-08-02T09:12:00.000Z", "actor": "planner", "action": "packet_created", "packetId": "wp-d4f004", "reasonCode": "contract_frozen_fanout"}),
    ("mid-flight/decisions.jsonl:6", {"at": "2026-08-02T09:12:00.000Z", "actor": "planner", "action": "packet_created", "packetId": "wp-e5g005", "reasonCode": "integration_barrier"}),
    ("mid-flight/decisions.jsonl:7", {"at": "2026-08-02T09:14:03.000Z", "actor": "manager", "action": "lock_acquired", "packetId": "wp-b2d002", "reasonCode": "path_free", "detail": "src/auth/"}),
    ("mid-flight/decisions.jsonl:8", {"at": "2026-08-02T09:14:07.000Z", "actor": "manager", "action": "lock_acquired", "packetId": "wp-c3e003", "reasonCode": "path_free", "detail": "src/api/"}),
    ("mid-flight/decisions.jsonl:9", {"at": "2026-08-02T09:15:38.000Z", "actor": "guardian", "action": "tool_blocked", "packetId": "wp-c3e003", "reasonCode": "path_readonly", "detail": "尝试写 tests/api/auth.spec.ts —— 只读挂载，已拦截"}),
    ("mid-flight/decisions.jsonl:10", {"at": "2026-08-02T09:15:41.000Z", "actor": "manager", "action": "lock_acquired", "packetId": "wp-d4f004", "reasonCode": "path_free", "detail": "src/web/"}),
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
    ("删掉必填字段 at", {"actor": "integrator", "action": "packet_accepted", "packetId": "wp-a1c001", "reasonCode": "gate_passed", "detail": "acceptance"}),
    ("删掉必填字段 actor", {"at": "2026-08-02T09:11:44.000Z", "action": "packet_accepted", "packetId": "wp-a1c001", "reasonCode": "gate_passed", "detail": "acceptance"}),
    ("删掉必填字段 action", {"at": "2026-08-02T09:11:44.000Z", "actor": "integrator", "packetId": "wp-a1c001", "reasonCode": "gate_passed", "detail": "acceptance"}),
    ("删掉必填字段 reasonCode", {"at": "2026-08-02T09:11:44.000Z", "actor": "integrator", "action": "packet_accepted", "packetId": "wp-a1c001", "detail": "acceptance"}),
    ("加一个未声明的字段（additionalProperties: false）", {"at": "2026-08-02T09:11:44.000Z", "actor": "integrator", "action": "packet_accepted", "packetId": "wp-a1c001", "reasonCode": "gate_passed", "detail": "acceptance", "__未声明的字段__": 1}),
]


@pytest.mark.parametrize(("why", "value"), NEGATIVES, ids=[n[0] for n in NEGATIVES])
def test_negative(why: str, value: object) -> None:
    assert check_schema(value) != [], f"这份数据违反契约，schema 校验却放行了：{why}"
    assert check_model(value) != [], f"这份数据违反契约，Pydantic 模型却放行了：{why}"
