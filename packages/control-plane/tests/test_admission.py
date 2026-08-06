"""admission 准入校验器测试

覆盖：
  · I2 自评审禁止
  · 路径校验
  · 预算校验
  · 依赖 DAG 检查
  · 角色存在性校验（与 B 的 RoleSpec 对接）
  · 模型隔离校验（mustDifferFrom）
  · AdmissionChecker 集成
"""

from __future__ import annotations

import pytest

from codentum_contracts.state import (
    DependencyGraph,
    PacketId,
    RoleId,
    RoleSpec,
    WorkPacket,
)
from codentum_control_plane.admission import (
    AdmissionChecker,
    AdmissionVerdict,
    Violation,
    DEFAULT_RULES,
)
from codentum_control_plane.admission.rules import (
    check_self_review,
    check_owns_paths,
    check_budget_limit,
    check_budget_degradation,
    check_deps_no_self_ref,
    check_deps_dag,
    check_role_exists,
    check_role_model_isolation,
)


def _pkt(
    pid: str = "wp-test0001",
    state: str = "pending",
    role: str = "coder",
    ownsPaths: tuple[str, ...] = ("src/test/",),
    readsPaths: tuple[str, ...] = (),
    deps: tuple[str, ...] = (),
    acceptance_predicate: str = "pytest",
    acceptance_authoredBy: str = "qa",
    limitCny: float = 5.0,
    degradationChain: tuple[str, ...] = ("drop_semantic",),
    routing_model: str | None = "qwen-plus",
    routing_effort: str = "medium",
) -> WorkPacket:
    return WorkPacket(
        id=PacketId(pid),
        kind="impl",
        state=state,  # type: ignore[arg-type]
        role=role,  # type: ignore[arg-type]
        ownsPaths=ownsPaths,
        readsPaths=readsPaths,
        deps=tuple(PacketId(d) for d in deps),
        acceptance={
            "kind": "test",
            "predicate": acceptance_predicate,
            "authoredBy": acceptance_authoredBy,
        },
        budget={
            "currency": "CNY",
            "limitCny": limitCny,
            "spentCny": 0.0,
            "degradationChain": degradationChain,
        },
        routing=(
            {"model": routing_model, "effort": routing_effort}
            if routing_model
            else None
        ),
        attempts=0,
        evidence=(),
        provenance={
            "createdBy": "planner",
            "createdAt": "2026-08-05T00:00:00Z",
        },
    )


def _coder_spec() -> RoleSpec:
    """对应 B 的 specs/coder.json"""
    return RoleSpec(
        id="coder",  # type: ignore[arg-type]
        summary="实现 WorkPacket",
        usesModel=True,
        writes=("workspace/**",),
        reads=("packages/contracts/**", "tests/**"),
        tools=("read_file", "write_file", "run_tests"),
        transitions=(
            {"from": "running", "to": "review", "requiresGate": "self-test"},
            {"from": "running", "to": "blocked"},
        ),
        modelPolicy={
            "defaultModel": "qwen-plus",
            "defaultEffort": "medium",
        },
    )


def _qa_spec() -> RoleSpec:
    """对应 B 的 specs/qa.json"""
    return RoleSpec(
        id="qa",  # type: ignore[arg-type]
        summary="写验收测试",
        usesModel=True,
        writes=("tests/acceptance/**",),
        reads=("packages/contracts/**",),
        tools=("write_acceptance_tests",),
        transitions=(
            {"from": "ready", "to": "running"},
            {"from": "running", "to": "review", "requiresGate": "acceptance"},
        ),
        modelPolicy={
            "defaultModel": "qwen-max",
            "defaultEffort": "high",
            "mustDifferFrom": ("coder",),
        },
    )


def _reviewer_spec() -> RoleSpec:
    """对应 B 的 specs/reviewer.json"""
    return RoleSpec(
        id="reviewer",  # type: ignore[arg-type]
        summary="对抗评审",
        usesModel=True,
        writes=("evidence/reviews/**",),
        reads=("packages/contracts/**", "tests/**"),
        tools=("read_file", "read_diff", "write_review"),
        transitions=(
            {"from": "review", "to": "accepted", "requiresGate": "review"},
            {"from": "review", "to": "rejected", "requiresGate": "review"},
        ),
        modelPolicy={
            "defaultModel": "qwen-max",
            "defaultEffort": "high",
            "mustDifferFrom": ("coder",),
        },
    )


SPECS = (_coder_spec(), _qa_spec(), _reviewer_spec())


# ════════════════════════════════════════════════════════════
#  I2 自评审禁止
# ════════════════════════════════════════════════════════════


class TestSelfReview:
    def test_coder_self_review_rejected(self) -> None:
        """Coder 不能给自己的 packet 定验收。"""
        pkt = _pkt(role="coder", acceptance_authoredBy="coder")
        v = check_self_review(pkt)
        assert v is not None
        assert v.code == "I2_SELF_REVIEW"

    def test_qa_authored_allowed(self) -> None:
        """QA 给 Coder 写验收 —— 合法。"""
        pkt = _pkt(role="coder", acceptance_authoredBy="qa")
        v = check_self_review(pkt)
        assert v is None

    def test_empty_predicate_rejected(self) -> None:
        """空验收谓词被拒。"""
        pkt = _pkt(acceptance_predicate="   ", acceptance_authoredBy="qa")
        v = check_self_review(pkt)
        assert v is not None
        assert v.code == "I2_NO_ACCEPTANCE"


# ════════════════════════════════════════════════════════════
#  路径校验
# ════════════════════════════════════════════════════════════


class TestOwnsPaths:
    def test_empty_owns_rejected(self) -> None:
        pkt = _pkt(ownsPaths=())
        v = check_owns_paths(pkt)
        assert v is not None
        assert v.code == "OWNSPATHS_EMPTY"

    def test_owns_reads_overlap_rejected(self) -> None:
        pkt = _pkt(ownsPaths=("src/",), readsPaths=("src/", "docs/"))
        v = check_owns_paths(pkt)
        assert v is not None
        assert v.code == "OWNSPATHS_OVERLAP_READS"

    def test_non_overlapping_allowed(self) -> None:
        pkt = _pkt(ownsPaths=("src/",), readsPaths=("docs/", "tests/"))
        v = check_owns_paths(pkt)
        assert v is None


# ════════════════════════════════════════════════════════════
#  预算校验
# ════════════════════════════════════════════════════════════


class TestBudget:
    def test_zero_limit_rejected(self) -> None:
        pkt = _pkt(limitCny=0)
        v = check_budget_limit(pkt)
        assert v is not None
        assert v.code == "BUDGET_ZERO_LIMIT"

    def test_negative_limit_rejected(self) -> None:
        pkt = _pkt(limitCny=-1)
        v = check_budget_limit(pkt)
        assert v is not None
        assert v.code == "BUDGET_ZERO_LIMIT"

    def test_empty_degradation_rejected(self) -> None:
        pkt = _pkt(degradationChain=(), limitCny=5.0)
        v = check_budget_degradation(pkt)
        assert v is not None
        assert v.code == "BUDGET_NO_DEGRADATION"

    def test_valid_budget_allowed(self) -> None:
        pkt = _pkt(limitCny=5.0, degradationChain=("drop_semantic",))
        v1 = check_budget_limit(pkt)
        assert v1 is None
        v2 = check_budget_degradation(pkt)
        assert v2 is None


# ════════════════════════════════════════════════════════════
#  依赖校验
# ════════════════════════════════════════════════════════════


class TestDeps:
    def test_self_ref_rejected(self) -> None:
        pkt = _pkt(pid="wp-test0001", deps=("wp-test0001",))
        v = check_deps_no_self_ref(pkt)
        assert v is not None
        assert v.code == "DEPS_SELF_REF"

    def test_no_deps_allowed(self) -> None:
        pkt = _pkt(deps=())
        v = check_deps_no_self_ref(pkt)
        assert v is None

    def test_cycle_detected(self) -> None:
        """A dep B, B dep A → 环。"""
        a = _pkt(pid="wp-aaaa0001", deps=("wp-bbbb0001",))
        b = _pkt(pid="wp-bbbb0001", deps=())
        existing = {a.id: a, b.id: b}
        # 现在加一条 b dep a —— 应该检测到环
        new_b = _pkt(pid="wp-bbbb0001", deps=("wp-aaaa0001",))
        v = check_deps_dag(new_b, existing_packets=existing)
        assert v is not None
        assert v.code == "DEPS_CYCLE"

    def test_chain_no_cycle_allowed(self) -> None:
        """A dep B dep C：无环，合法。"""
        a = _pkt(pid="wp-aaaa0001", deps=("wp-bbbb0001",))
        b = _pkt(pid="wp-bbbb0001", deps=("wp-cccc0001",))
        c = _pkt(pid="wp-cccc0001", deps=())
        existing = {a.id: a, b.id: b, c.id: c}
        new_d = _pkt(pid="wp-dddd0001", deps=("wp-aaaa0001",))
        v = check_deps_dag(new_d, existing_packets=existing)
        assert v is None

    def test_three_node_cycle(self) -> None:
        """A dep B, B dep C, C dep A → 三节点环。"""
        a = _pkt(pid="wp-aaaa0001", deps=("wp-bbbb0001",))
        b = _pkt(pid="wp-bbbb0001", deps=("wp-cccc0001",))
        c = _pkt(pid="wp-cccc0001", deps=())
        existing = {a.id: a, b.id: b, c.id: c}
        # c dep a → cycle
        new_c = _pkt(pid="wp-cccc0001", deps=("wp-aaaa0001",))
        v = check_deps_dag(new_c, existing_packets=existing)
        assert v is not None
        assert v.code == "DEPS_CYCLE"


# ════════════════════════════════════════════════════════════
#  角色校验（与 B 的 RoleSpec 对接）
# ════════════════════════════════════════════════════════════


class TestRoleExists:
    def test_role_not_in_specs_rejected(self) -> None:
        pkt = _pkt(role="guardian")
        v = check_role_exists(pkt, role_specs=SPECS)
        assert v is not None
        assert v.code == "ROLE_NOT_FOUND"

    def test_author_not_in_specs_rejected(self) -> None:
        pkt = _pkt(role="coder", acceptance_authoredBy="guardian")
        v = check_role_exists(pkt, role_specs=SPECS)
        assert v is not None
        assert v.code == "ROLE_AUTHOR_NOT_FOUND"

    def test_valid_roles_allowed(self) -> None:
        pkt = _pkt(role="coder", acceptance_authoredBy="qa")
        v = check_role_exists(pkt, role_specs=SPECS)
        assert v is None

    def test_no_specs_skips_check(self) -> None:
        """无 RoleSpec 时不强制校验。"""
        pkt = _pkt(role="guardian")
        v = check_role_exists(pkt, role_specs=None)
        assert v is None


# ════════════════════════════════════════════════════════════
#  模型隔离校验（B 的 mustDifferFrom）
# ════════════════════════════════════════════════════════════


class TestModelIsolation:
    def test_same_model_violates_isolation(self) -> None:
        """Reviewer 用 qwen-plus（和 coder 同模型）→ 违规。"""
        pkt = _pkt(
            pid="wp-review01", role="reviewer",
            routing_model="qwen-plus"  # coder 的 defaultModel
        )
        v = check_role_model_isolation(pkt, role_specs=SPECS)
        assert v is not None
        assert v.code == "MODEL_ISOLATION"

    def test_different_model_allowed(self) -> None:
        """Reviewer 用不同模型 → 合法。"""
        pkt = _pkt(
            pid="wp-review02", role="reviewer",
            routing_model="qwen-max"  # 不是 coder 的 qwen-plus
        )
        v = check_role_model_isolation(pkt, role_specs=SPECS)
        assert v is None

    def test_coder_no_isolation_required(self) -> None:
        """Coder 没有 mustDifferFrom → 不检查模型隔离。"""
        pkt = _pkt(role="coder", routing_model="qwen-plus")
        v = check_role_model_isolation(pkt, role_specs=SPECS)
        assert v is None

    def test_no_routing_skips(self) -> None:
        """没有 routing 时跳过模型隔离检查。"""
        pkt = _pkt(role="reviewer", routing_model=None)
        v = check_role_model_isolation(pkt, role_specs=SPECS)
        assert v is None


# ════════════════════════════════════════════════════════════
#  AdmissionChecker 集成测试
# ════════════════════════════════════════════════════════════


class TestAdmissionChecker:
    def test_valid_packet_passes_all(self) -> None:
        checker = AdmissionChecker(role_specs=SPECS)
        pkt = _pkt()
        verdict = checker.check(pkt)
        assert verdict.allowed
        assert len(verdict.violations) == 0

    def test_multiple_violations_collected(self) -> None:
        """包涵多个问题的 packet 应该收集全部违规，而不止第一条。"""
        checker = AdmissionChecker(role_specs=SPECS)
        pkt = _pkt(
            pid="wp-test0001",
            role="coder",
            acceptance_authoredBy="coder",  # I2_SELF_REVIEW
            ownsPaths=(),                   # OWNSPATHS_EMPTY
            limitCny=0,                     # BUDGET_ZERO_LIMIT
            degradationChain=(),            # BUDGET_NO_DEGRADATION
        )
        verdict = checker.check(pkt)
        assert not verdict.allowed
        codes = {v.code for v in verdict.violations}
        assert "I2_SELF_REVIEW" in codes
        assert "OWNSPATHS_EMPTY" in codes
        assert "BUDGET_ZERO_LIMIT" in codes
        assert "BUDGET_NO_DEGRADATION" in codes

    def test_verdict_bool(self) -> None:
        checker = AdmissionChecker()
        assert AdmissionVerdict(allowed=True)
        assert not AdmissionVerdict(allowed=False)

    def test_custom_rules(self) -> None:
        """可以注入自定义规则。"""
        def always_reject(pkt, **_ctx):
            return Violation(code="I2_SELF_REVIEW", detail="custom")
        checker = AdmissionChecker(rules=(always_reject,))
        verdict = checker.check(_pkt())
        assert not verdict.allowed

    def test_model_isolation_integration(self) -> None:
        """完整的模型隔离流程：Reviewer 用 coder 同模型被准入拒。"""
        checker = AdmissionChecker(role_specs=SPECS)
        pkt = _pkt(
            pid="wp-review03",
            role="reviewer",
            acceptance_authoredBy="qa",
            routing_model="qwen-plus",  # coder 同模型
        )
        verdict = checker.check(pkt)
        assert not verdict.allowed
        assert any(v.code == "MODEL_ISOLATION" for v in verdict.violations)

    def test_full_valid_pipeline(self) -> None:
        """正常的 Coder packet（QA 写验收）：全部通过。"""
        checker = AdmissionChecker(role_specs=SPECS)
        pkt = _pkt(
            pid="wp-coder01",
            role="coder",
            acceptance_authoredBy="qa",
            ownsPaths=("src/feature/",),
            readsPaths=("docs/", "tests/"),
            deps=(),
            routing_model="qwen-plus",
        )
        verdict = checker.check(pkt)
        assert verdict.allowed, f"Violations: {[(v.code, v.detail) for v in verdict.violations]}"