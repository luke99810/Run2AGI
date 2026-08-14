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

from typing import cast

import pytest

from codentum_contracts.state import (
    Acceptance,
    BudgetGrant,
    DependencyGraph,
    ModelPolicy,
    ModelRouting,
    PacketId,
    Provenance,
    RoleId,
    RoleSpec,
    RoleTransition,
    WorkPacket,
)
from codentum_control_plane.admission import (
    AdmissionChecker,
    AdmissionVerdict,
    Violation,
    DEFAULT_RULES,
    check_predicate_covers_owned_paths,
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
        acceptance=Acceptance(
            kind= "test",
            predicate= acceptance_predicate,
            authoredBy=cast(RoleId, acceptance_authoredBy),
        ),
        budget=BudgetGrant(
            currency= "CNY",
            limitCny= limitCny,
            spentCny= 0.0,
            degradationChain= degradationChain,
        ),
        routing=(
            ModelRouting.model_validate({"model": routing_model, "effort": routing_effort})
            if routing_model
            else None
        ),
        attempts=0,
        evidence=(),
        provenance=Provenance(
            createdBy= "planner",
            createdAt= "2026-08-05T00:00:00Z",
        ),
    )


def _coder_spec() -> RoleSpec:
    """对应 B 的 specs/coder.json"""
    return RoleSpec(
        id="coder",
        summary="实现 WorkPacket",
        usesModel=True,
        writes=("workspace/**",),
        reads=("packages/contracts/**", "tests/**"),
        tools=("read_file", "write_file", "run_tests"),
        transitions=(
            RoleTransition.model_validate({"from": "running", "to": "review", "requiresGate": "self-test"}),
            RoleTransition.model_validate({"from": "running", "to": "blocked"}),
        ),
        modelPolicy=ModelPolicy(
            defaultModel= "qwen-plus",
            defaultEffort= "medium",
        ),
    )


def _qa_spec() -> RoleSpec:
    """对应 B 的 specs/qa.json"""
    return RoleSpec(
        id="qa",
        summary="写验收测试",
        usesModel=True,
        writes=("tests/acceptance/**",),
        reads=("packages/contracts/**",),
        tools=("write_acceptance_tests",),
        transitions=(
            RoleTransition.model_validate({"from": "ready", "to": "running"}),
            RoleTransition.model_validate({"from": "running", "to": "review", "requiresGate": "acceptance"}),
        ),
        modelPolicy=ModelPolicy(
            defaultModel= "qwen-max",
            defaultEffort= "high",
            mustDifferFrom= ("coder",),
        ),
    )


def _reviewer_spec() -> RoleSpec:
    """对应 B 的 specs/reviewer.json"""
    return RoleSpec(
        id="reviewer",
        summary="对抗评审",
        usesModel=True,
        writes=("evidence/reviews/**",),
        reads=("packages/contracts/**", "tests/**"),
        tools=("read_file", "read_diff", "write_review"),
        transitions=(
            RoleTransition.model_validate({"from": "review", "to": "accepted", "requiresGate": "review"}),
            RoleTransition.model_validate({"from": "review", "to": "rejected", "requiresGate": "review"}),
        ),
        modelPolicy=ModelPolicy(
            defaultModel= "qwen-max",
            defaultEffort= "high",
            mustDifferFrom= ("coder",),
        ),
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
        def always_reject(pkt: WorkPacket, **_ctx: object) -> Violation | None:
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

# ════════════════════════════════════════════════════════════
#  弱变异检验补上的三条
#
#  ★ 来源：scripts/mutate_judgements.py --mode=weak。
#    这三处在强变异（整条判据摘掉）下都是被杀死的 —— 说明有人碰过它们；
#    但在弱变异（边界挪一格）下存活，说明**边界那一格没人测过**。
#
#    两个数测的是两件事：
#      强变异存活率 = 有没有人**碰过**这条判据
#      弱变异存活率 = 有没有人**测准**这条判据的边界
# ════════════════════════════════════════════════════════════


class TestJudgementBoundaries:
    def test_small_but_valid_budget_is_accepted(self) -> None:
        """★ 合法预算的**下边界**必须被测。

        原先只有「0 无效」和「5.0 有效」两条 —— 中间那段没人测过。
        后果是 `<= 0` 被改成 `<= 1` 时全套测试照样绿：
        所有 0 到 1 元的合法预算会被静默拒绝，而没有任何信号。
        """

        assert check_budget_limit(_pkt(limitCny=0.5)) is None

    def test_isolation_defers_when_role_is_unknown(self) -> None:
        """★ packet.role 不在 role_specs 里时，模型隔离**让路**而不是报错。

        这条卫语句的语义是分工：「角色不存在」由 ROLE_NOT_FOUND 负责报，
        这里再报一次就是同一个问题出两条违规。

        它原先无人覆盖 —— 把 `spec is None or spec.modelPolicy is None`
        改成 `and` 之后没有任何测试变红，而那个改动会让这条路径直接
        抛 AttributeError。
        """

        pkt = _pkt(role="architect", routing_model="qwen-plus")
        assert check_role_model_isolation(pkt, role_specs=SPECS) is None

    def test_isolation_skips_role_absent_from_the_given_specs(self) -> None:
        """★ mustDifferFrom 指向的角色不在**本次传入的 role_specs** 里时跳过，不崩。

        写这条测试时先试了另一个场景 —— 让 mustDifferFrom 指向一个
        不存在的角色名 —— 发现**它在类型层面就不可表示**：
        mustDifferFrom 是 Literal 枚举，pydantic 在构造时就拒了。

        这正是本项目那条约束实现优先级的一个实例：
        **不可见 > 无权限 > 被拦截 > 提示词劝阻**。
        不可表示的东西不需要运行时检查。

        所以这条卫语句真正守的是另一件事：角色名合法，但调用方传进来的
        role_specs 是**不完整的**（被过滤过、或分批加载）。
        这种情况下这条约束无法判定，应当跳过 ——
        而不是让一个局部视图把整个准入流程炸掉。
        """

        partial = _reviewer_spec().model_copy(
            update={"modelPolicy": ModelPolicy(
                defaultModel="qwen-max", defaultEffort="high",
                mustDifferFrom=("manager",),
            )}
        )
        pkt = _pkt(role="reviewer", routing_model="qwen-plus")
        # ★ 注意 role_specs 里**没有** manager
        assert check_role_model_isolation(pkt, role_specs=(partial,)) is None


# ════════════════════════════════════════════════════════════
#  影子判据与晋级门（算子四）
# ════════════════════════════════════════════════════════════


class TestShadowJudgements:
    def test_shadow_rule_fires_but_does_not_block(self) -> None:
        """★ 影子档位的全部意义：**评估、记录，但不拦**。

        一条新判据在没有真实数据支撑之前，你不知道它会不会误伤。
        直接上线拦截，第一次误拦就会让人把它整条关掉 ——
        **而关掉之后它再也不会被打开。** 影子期让它先攒证据。
        """

        from codentum_control_plane.admission.checker import AdmissionChecker

        # ownsPaths 是 src/test/，谓词只写 "pytest" —— 那条影子规则会命中
        packet = _pkt(ownsPaths=("src/test/",), acceptance_predicate="pytest")
        assert check_predicate_covers_owned_paths(packet) is not None, "前提不成立：影子规则没命中"

        verdict = AdmissionChecker(role_specs=SPECS).check(packet)
        codes = {v.code for v in verdict.violations}
        assert "PREDICATE_SCOPE_MISMATCH" not in codes, "影子判据拦人了"

    def test_unregistered_rule_defaults_to_enforcing_not_shadow(self) -> None:
        """★ 方向不能反。

        默认 shadow → 新加的规则不生效，而这件事是**静默的**
                      （它躺在判据集里，实际什么都不拦）
        默认 enforcing → 可能误拦，但那是**响亮的**

        响亮的失败优于静默的失败。这条钉住这个方向。
        """

        from codentum_control_plane.admission.checker import AdmissionChecker

        def always_fires(_packet: object, **_ctx: object) -> Violation:
            return Violation(code="BUDGET_ZERO_LIMIT", detail="没登记档位的新规则", field=None)

        verdict = AdmissionChecker(rules=(always_fires,), role_specs=SPECS).check(_pkt())
        assert not verdict.allowed, "没登记档位的规则被当成 shadow 放行了"

    def test_shadow_hits_are_recorded(self) -> None:
        """★ 影子期不记录 = 晋级永远无据可依。

        晋级到 enforcing 的第一个条件是「在真实案例上命中过 ≥1 次」——
        没有命中记录，这个条件**永远无法被满足**，
        于是影子判据会永远停在影子里。那和不加这条判据是一样的。
        """

        from codentum_control_plane.admission.checker import AdmissionChecker

        seen: list[tuple[str, str, bool, str | None]] = []
        AdmissionChecker(
            role_specs=SPECS,
            recorder=lambda _pid, rule, mode, fired, code: seen.append((rule, mode, fired, code)),
        ).check(_pkt(ownsPaths=("src/test/",), acceptance_predicate="pytest"))

        shadow_hits = [row for row in seen if row[0] == "check_predicate_covers_owned_paths"]
        assert shadow_hits == [("check_predicate_covers_owned_paths", "shadow", True, "PREDICATE_SCOPE_MISMATCH")]

    def test_enforcing_rules_are_recorded_too(self) -> None:
        """记录不能只记命中的 —— 「跑了但没命中」也是数据。

        资产负债表要靠它区分「这条判据从没命中过」与「没人在记录」。
        """

        from codentum_control_plane.admission.checker import AdmissionChecker

        seen: list[str] = []
        AdmissionChecker(
            role_specs=SPECS,
            recorder=lambda _pid, rule, _mode, _fired, _code: seen.append(rule),
        ).check(_pkt())
        assert "check_budget_limit" in seen, "没命中的规则也该留下运行记录"


class TestPredicateScopeShadowRule:
    def test_predicate_on_the_same_path_chain_is_accepted(self) -> None:
        """★ 用**路径链关系**而不是字符串包含。

        谓词范围更大（workspace ⊃ workspace/alpha/src）算覆盖，
        更小但在链上也算。
        """

        wider = _pkt(ownsPaths=("workspace/alpha/src/",), acceptance_predicate="pytest workspace -q")
        narrower = _pkt(ownsPaths=("workspace/",), acceptance_predicate="pytest workspace/alpha -q")
        assert check_predicate_covers_owned_paths(wider) is None
        assert check_predicate_covers_owned_paths(narrower) is None

    def test_sibling_path_is_flagged(self) -> None:
        """★ 拥有 alpha 却去验 beta —— 谓词会绿，但它验的是别人的产出。"""

        packet = _pkt(
            ownsPaths=("workspace/alpha/",), acceptance_predicate="pytest workspace/beta -q"
        )
        assert check_predicate_covers_owned_paths(packet) is not None

    def test_substring_lookalike_is_not_a_match(self) -> None:
        """★ 纯 `in` 判断会让 workspace 匹配上 my-workspace-backup。"""

        packet = _pkt(
            ownsPaths=("workspace/",), acceptance_predicate="pytest my-workspace-backup -q"
        )
        assert check_predicate_covers_owned_paths(packet) is not None
