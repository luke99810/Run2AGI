"""契约测试：evidence

⚠️ 本文件由 `python scripts/gen_contract_tests.py` 生成。【不要手改】—— 下次生成会覆盖。

测的不是「我们的固件对不对」（那是 validate_fixtures 的事），
而是【任何实现都必须满足的契约行为】：合法的必须过，非法的必须拒。

★ 反例由真实固件变异而来，不是凭空造的 ——
  凭空造的实例容易在别处就不合法，于是测试通过的原因与它想测的东西无关。

★ 每条用例都走两条校验路径，两条都必须一致地判定：
    schema 层  scripts/lib/schema.py（零依赖）
    模型层     codentum_contracts.Evidence（Pydantic 运行时校验）
  两者不一致 = 生成器与 schema 之间出现了缝，而那是跨语言漂移的入口。

跑：pytest tests/contract

真源：packages/contracts/schemas/evidence.schema.json
生成器：scripts/gen_contract_tests.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "packages" / "contracts" / "python"))

from codentum_contracts import Evidence  # noqa: E402
from lib.schema import load_schemas, validate  # noqa: E402

SCHEMA_FILE = "evidence.schema.json"
_SCHEMAS = load_schemas(ROOT / "packages" / "contracts" / "schemas")


def check_schema(v: object) -> list[str]:
    return validate(_SCHEMAS[SCHEMA_FILE], v, _SCHEMAS, SCHEMA_FILE)


def check_model(v: object) -> list[str]:
    try:
        Evidence.model_validate(v)
    except Exception as e:  # noqa: BLE001
        return [str(e)]
    return []

# ══════════════════════════════════════════════════════════
#  正例：5 份真实固件必须全部通过
# ══════════════════════════════════════════════════════════

POSITIVES = [
    ("blocked/evidence/ev-001.json", {"ref": "ev-001", "packetId": "wp-a1c001", "role": "architect", "kind": "gate", "gate": "acceptance", "verdict": "pass", "artifacts": ["sha256:3f1a9c0b7d2e845169acbf03e5d7128a4b6c9e2f0a1d3b5c7e9f1a2b4c6d8e0f"], "prevDigest": "sha256:0000000000000000000000000000000000000000000000000000000000000000", "digest": "sha256:9b2e4d6f8a0c1e3b5d7f9a1c3e5b7d9f1a3c5e7b9d1f3a5c7e9b1d3f5a7c9e1b", "at": "2026-08-02T09:11:44.000Z", "detail": "契约冻结：gen:check 无 diff，typecheck 通过"}),
    ("blocked/evidence/ev-011.json", {"ref": "ev-011", "packetId": "wp-b2d002", "role": "coder", "kind": "test_run", "verdict": "fail", "artifacts": ["sha256:11aa22bb33cc44dd55ee66ff778899001122334455667788990011223344aabb"], "prevDigest": "sha256:0000000000000000000000000000000000000000000000000000000000000000", "digest": "sha256:a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90", "at": "2026-08-02T09:52:10.000Z", "detail": "attempt 1/3：tests/auth 4 项未过"}),
    ("blocked/evidence/ev-012.json", {"ref": "ev-012", "packetId": "wp-b2d002", "role": "coder", "kind": "test_run", "verdict": "fail", "artifacts": ["sha256:22bb33cc44dd55ee66ff77889900112233445566778899001122334455bbcc00"], "prevDigest": "sha256:a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90", "digest": "sha256:b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90a1", "at": "2026-08-02T10:14:33.000Z", "detail": "attempt 2/3：session 过期判定仍未过 —— 与 attempt 1 同一处失败，触发 L0.5 Peer-Debug"}),
    ("blocked/evidence/ev-013.json", {"ref": "ev-013", "packetId": "wp-b2d002", "role": "coder", "kind": "test_run", "verdict": "fail", "artifacts": ["sha256:33cc44dd55ee66ff7788990011223344556677889900112233445566ccdd0011"], "prevDigest": "sha256:b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90a1", "digest": "sha256:c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2", "at": "2026-08-02T10:40:58.000Z", "detail": "attempt 3/3：packet 预算 2.50 USD 用尽，中止并释放 src/auth/ 锁，升级至 L1 Helper"}),
    ("mid-flight/evidence/ev-001.json", {"ref": "ev-001", "packetId": "wp-a1c001", "role": "architect", "kind": "gate", "gate": "acceptance", "verdict": "pass", "artifacts": ["sha256:3f1a9c0b7d2e845169acbf03e5d7128a4b6c9e2f0a1d3b5c7e9f1a2b4c6d8e0f"], "prevDigest": "sha256:0000000000000000000000000000000000000000000000000000000000000000", "digest": "sha256:9b2e4d6f8a0c1e3b5d7f9a1c3e5b7d9f1a3c5e7b9d1f3a5c7e9b1d3f5a7c9e1b", "at": "2026-08-02T09:11:44.000Z", "detail": "契约冻结：gen:check 无 diff，typecheck 通过"}),
]


@pytest.mark.parametrize(("fixture_id", "value"), POSITIVES, ids=[p[0] for p in POSITIVES])
def test_positive(fixture_id: str, value: object) -> None:
    assert check_schema(value) == [], f"合法固件不应报错：{fixture_id}"
    assert check_model(value) == [], f"Pydantic 模型拒绝了合法固件：{fixture_id}"

# ══════════════════════════════════════════════════════════
#  反例：13 种变异必须【全部被拒】
#
#  ★ 这些不是"边界情况"，是契约的定义本身。
#    任何一条变成绿灯，都说明校验器漏了一处，
#    而漏掉的那处正是 Agent 最可能踩的地方。
# ══════════════════════════════════════════════════════════

NEGATIVES = [
    ("删掉必填字段 ref", {"packetId": "wp-a1c001", "role": "architect", "kind": "gate", "gate": "acceptance", "verdict": "pass", "artifacts": ["sha256:3f1a9c0b7d2e845169acbf03e5d7128a4b6c9e2f0a1d3b5c7e9f1a2b4c6d8e0f"], "prevDigest": "sha256:0000000000000000000000000000000000000000000000000000000000000000", "digest": "sha256:9b2e4d6f8a0c1e3b5d7f9a1c3e5b7d9f1a3c5e7b9d1f3a5c7e9b1d3f5a7c9e1b", "at": "2026-08-02T09:11:44.000Z", "detail": "契约冻结：gen:check 无 diff，typecheck 通过"}),
    ("删掉必填字段 packetId", {"ref": "ev-001", "role": "architect", "kind": "gate", "gate": "acceptance", "verdict": "pass", "artifacts": ["sha256:3f1a9c0b7d2e845169acbf03e5d7128a4b6c9e2f0a1d3b5c7e9f1a2b4c6d8e0f"], "prevDigest": "sha256:0000000000000000000000000000000000000000000000000000000000000000", "digest": "sha256:9b2e4d6f8a0c1e3b5d7f9a1c3e5b7d9f1a3c5e7b9d1f3a5c7e9b1d3f5a7c9e1b", "at": "2026-08-02T09:11:44.000Z", "detail": "契约冻结：gen:check 无 diff，typecheck 通过"}),
    ("删掉必填字段 role", {"ref": "ev-001", "packetId": "wp-a1c001", "kind": "gate", "gate": "acceptance", "verdict": "pass", "artifacts": ["sha256:3f1a9c0b7d2e845169acbf03e5d7128a4b6c9e2f0a1d3b5c7e9f1a2b4c6d8e0f"], "prevDigest": "sha256:0000000000000000000000000000000000000000000000000000000000000000", "digest": "sha256:9b2e4d6f8a0c1e3b5d7f9a1c3e5b7d9f1a3c5e7b9d1f3a5c7e9b1d3f5a7c9e1b", "at": "2026-08-02T09:11:44.000Z", "detail": "契约冻结：gen:check 无 diff，typecheck 通过"}),
    ("删掉必填字段 kind", {"ref": "ev-001", "packetId": "wp-a1c001", "role": "architect", "gate": "acceptance", "verdict": "pass", "artifacts": ["sha256:3f1a9c0b7d2e845169acbf03e5d7128a4b6c9e2f0a1d3b5c7e9f1a2b4c6d8e0f"], "prevDigest": "sha256:0000000000000000000000000000000000000000000000000000000000000000", "digest": "sha256:9b2e4d6f8a0c1e3b5d7f9a1c3e5b7d9f1a3c5e7b9d1f3a5c7e9b1d3f5a7c9e1b", "at": "2026-08-02T09:11:44.000Z", "detail": "契约冻结：gen:check 无 diff，typecheck 通过"}),
    ("删掉必填字段 verdict", {"ref": "ev-001", "packetId": "wp-a1c001", "role": "architect", "kind": "gate", "gate": "acceptance", "artifacts": ["sha256:3f1a9c0b7d2e845169acbf03e5d7128a4b6c9e2f0a1d3b5c7e9f1a2b4c6d8e0f"], "prevDigest": "sha256:0000000000000000000000000000000000000000000000000000000000000000", "digest": "sha256:9b2e4d6f8a0c1e3b5d7f9a1c3e5b7d9f1a3c5e7b9d1f3a5c7e9b1d3f5a7c9e1b", "at": "2026-08-02T09:11:44.000Z", "detail": "契约冻结：gen:check 无 diff，typecheck 通过"}),
    ("删掉必填字段 artifacts", {"ref": "ev-001", "packetId": "wp-a1c001", "role": "architect", "kind": "gate", "gate": "acceptance", "verdict": "pass", "prevDigest": "sha256:0000000000000000000000000000000000000000000000000000000000000000", "digest": "sha256:9b2e4d6f8a0c1e3b5d7f9a1c3e5b7d9f1a3c5e7b9d1f3a5c7e9b1d3f5a7c9e1b", "at": "2026-08-02T09:11:44.000Z", "detail": "契约冻结：gen:check 无 diff，typecheck 通过"}),
    ("删掉必填字段 prevDigest", {"ref": "ev-001", "packetId": "wp-a1c001", "role": "architect", "kind": "gate", "gate": "acceptance", "verdict": "pass", "artifacts": ["sha256:3f1a9c0b7d2e845169acbf03e5d7128a4b6c9e2f0a1d3b5c7e9f1a2b4c6d8e0f"], "digest": "sha256:9b2e4d6f8a0c1e3b5d7f9a1c3e5b7d9f1a3c5e7b9d1f3a5c7e9b1d3f5a7c9e1b", "at": "2026-08-02T09:11:44.000Z", "detail": "契约冻结：gen:check 无 diff，typecheck 通过"}),
    ("删掉必填字段 digest", {"ref": "ev-001", "packetId": "wp-a1c001", "role": "architect", "kind": "gate", "gate": "acceptance", "verdict": "pass", "artifacts": ["sha256:3f1a9c0b7d2e845169acbf03e5d7128a4b6c9e2f0a1d3b5c7e9f1a2b4c6d8e0f"], "prevDigest": "sha256:0000000000000000000000000000000000000000000000000000000000000000", "at": "2026-08-02T09:11:44.000Z", "detail": "契约冻结：gen:check 无 diff，typecheck 通过"}),
    ("删掉必填字段 at", {"ref": "ev-001", "packetId": "wp-a1c001", "role": "architect", "kind": "gate", "gate": "acceptance", "verdict": "pass", "artifacts": ["sha256:3f1a9c0b7d2e845169acbf03e5d7128a4b6c9e2f0a1d3b5c7e9f1a2b4c6d8e0f"], "prevDigest": "sha256:0000000000000000000000000000000000000000000000000000000000000000", "digest": "sha256:9b2e4d6f8a0c1e3b5d7f9a1c3e5b7d9f1a3c5e7b9d1f3a5c7e9b1d3f5a7c9e1b", "detail": "契约冻结：gen:check 无 diff，typecheck 通过"}),
    ("枚举字段 role 填非法值", {"ref": "ev-001", "packetId": "wp-a1c001", "role": "__不是合法枚举值__", "kind": "gate", "gate": "acceptance", "verdict": "pass", "artifacts": ["sha256:3f1a9c0b7d2e845169acbf03e5d7128a4b6c9e2f0a1d3b5c7e9f1a2b4c6d8e0f"], "prevDigest": "sha256:0000000000000000000000000000000000000000000000000000000000000000", "digest": "sha256:9b2e4d6f8a0c1e3b5d7f9a1c3e5b7d9f1a3c5e7b9d1f3a5c7e9b1d3f5a7c9e1b", "at": "2026-08-02T09:11:44.000Z", "detail": "契约冻结：gen:check 无 diff，typecheck 通过"}),
    ("枚举字段 kind 填非法值", {"ref": "ev-001", "packetId": "wp-a1c001", "role": "architect", "kind": "__不是合法枚举值__", "gate": "acceptance", "verdict": "pass", "artifacts": ["sha256:3f1a9c0b7d2e845169acbf03e5d7128a4b6c9e2f0a1d3b5c7e9f1a2b4c6d8e0f"], "prevDigest": "sha256:0000000000000000000000000000000000000000000000000000000000000000", "digest": "sha256:9b2e4d6f8a0c1e3b5d7f9a1c3e5b7d9f1a3c5e7b9d1f3a5c7e9b1d3f5a7c9e1b", "at": "2026-08-02T09:11:44.000Z", "detail": "契约冻结：gen:check 无 diff，typecheck 通过"}),
    ("枚举字段 verdict 填非法值", {"ref": "ev-001", "packetId": "wp-a1c001", "role": "architect", "kind": "gate", "gate": "acceptance", "verdict": "__不是合法枚举值__", "artifacts": ["sha256:3f1a9c0b7d2e845169acbf03e5d7128a4b6c9e2f0a1d3b5c7e9f1a2b4c6d8e0f"], "prevDigest": "sha256:0000000000000000000000000000000000000000000000000000000000000000", "digest": "sha256:9b2e4d6f8a0c1e3b5d7f9a1c3e5b7d9f1a3c5e7b9d1f3a5c7e9b1d3f5a7c9e1b", "at": "2026-08-02T09:11:44.000Z", "detail": "契约冻结：gen:check 无 diff，typecheck 通过"}),
    ("加一个未声明的字段（additionalProperties: false）", {"ref": "ev-001", "packetId": "wp-a1c001", "role": "architect", "kind": "gate", "gate": "acceptance", "verdict": "pass", "artifacts": ["sha256:3f1a9c0b7d2e845169acbf03e5d7128a4b6c9e2f0a1d3b5c7e9f1a2b4c6d8e0f"], "prevDigest": "sha256:0000000000000000000000000000000000000000000000000000000000000000", "digest": "sha256:9b2e4d6f8a0c1e3b5d7f9a1c3e5b7d9f1a3c5e7b9d1f3a5c7e9b1d3f5a7c9e1b", "at": "2026-08-02T09:11:44.000Z", "detail": "契约冻结：gen:check 无 diff，typecheck 通过", "__未声明的字段__": 1}),
]


@pytest.mark.parametrize(("why", "value"), NEGATIVES, ids=[n[0] for n in NEGATIVES])
def test_negative(why: str, value: object) -> None:
    assert check_schema(value) != [], f"这份数据违反契约，schema 校验却放行了：{why}"
    assert check_model(value) != [], f"这份数据违反契约，Pydantic 模型却放行了：{why}"
