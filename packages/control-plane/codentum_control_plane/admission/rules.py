"""Admission 校验规则 —— 每条规则是独立的纯函数。

每个规则只做一件事：接收 packet + 语境，返回 Violation 或 None。
不产生副作用，不读磁盘，不调模型。
新增校验 → 新加一个函数，不改已有函数。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from codentum_contracts.state import (
    DependencyGraph,
    PacketId,
    RoleSpec,
    WorkPacket,
)

__all__ = [
    "Violation",
    "check_self_review",
    "check_owns_paths",
    "check_budget_limit",
    "check_budget_degradation",
    "check_deps_no_self_ref",
    "check_deps_dag",
    "check_role_exists",
    "check_role_model_isolation",
    "DEFAULT_RULES",
    "RuleFn",
]


ViolationCode = Literal[
    "I2_SELF_REVIEW",
    "I2_NO_ACCEPTANCE",
    "OWNSPATHS_EMPTY",
    "OWNSPATHS_OVERLAP_READS",
    "BUDGET_ZERO_LIMIT",
    "BUDGET_NO_DEGRADATION",
    "DEPS_SELF_REF",
    "DEPS_CYCLE",
    "ROLE_NOT_FOUND",
    "ROLE_AUTHOR_NOT_FOUND",
    "MODEL_ISOLATION",
]


RuleFn = Callable[..., "Violation | None"]
"""一条校验规则的签名。★ 与 checker.py 共用同一个别名，两处各写一份迟早漂移。"""


@dataclass(frozen=True, slots=True)
class Violation:
    """一条准入违规。被拒是预期内的返回值，不是异常。"""
    code: ViolationCode
    detail: str
    field: str | None = None


def check_self_review(packet: WorkPacket, **_ctx: object) -> Violation | None:
    """I2: acceptance.authoredBy != packet.role"""
    if not packet.acceptance.predicate.strip():
        return Violation(
            code="I2_NO_ACCEPTANCE",
            detail="验收谓词为空。每个 packet 必须至少有一条机器可判定的验收谓词。",
            field="acceptance.predicate",
        )
    if packet.acceptance.authoredBy == packet.role:
        return Violation(
            code="I2_SELF_REVIEW",
            detail=f"角色 {packet.role!r} 不能给自己的 packet 定验收标准。",
            field="acceptance.authoredBy",
        )
    return None


def check_owns_paths(packet: WorkPacket, **_ctx: object) -> Violation | None:
    """写权限路径不能为空，且不能和只读路径重叠。"""
    if not packet.ownsPaths:
        return Violation(
            code="OWNSPATHS_EMPTY",
            detail="ownsPaths 为空。没有写权限的 packet 无法产生产出。",
            field="ownsPaths",
        )
    overlap = set(packet.ownsPaths) & set(packet.readsPaths)
    if overlap:
        return Violation(
            code="OWNSPATHS_OVERLAP_READS",
            detail=f"ownsPaths 与 readsPaths 存在交集：{sorted(overlap)}。",
            field="ownsPaths",
        )
    return None


def check_budget_limit(packet: WorkPacket, **_ctx: object) -> Violation | None:
    """预算额度必须 > 0。"""
    if packet.budget.limitCny <= 0:
        return Violation(
            code="BUDGET_ZERO_LIMIT",
            detail=f"预算额度必须 > 0，当前 ${packet.budget.limitCny}。",
            field="budget.limitCny",
        )
    return None


def check_budget_degradation(packet: WorkPacket, **_ctx: object) -> Violation | None:
    """降级链不能为空。"""
    if not packet.budget.degradationChain:
        return Violation(
            code="BUDGET_NO_DEGRADATION",
            detail="降级链不能为空。否则预算不足时随机截断上下文，不可复现。",
            field="budget.degradationChain",
        )
    return None


def check_deps_no_self_ref(packet: WorkPacket, **_ctx: object) -> Violation | None:
    """不允许自己依赖自己。"""
    if packet.id in packet.deps:
        return Violation(
            code="DEPS_SELF_REF",
            detail=f"{packet.id!r} 依赖列表包含自己。自依赖永远无法满足。",
            field="deps",
        )
    return None


def check_deps_dag(
    packet: WorkPacket,
    *,
    existing_packets: dict[PacketId, WorkPacket] | None = None,
    dep_graph: DependencyGraph | None = None,
    **_ctx: object,
) -> Violation | None:
    """检查加入此 packet 后依赖图是否仍为 DAG（DFS 染色法）。"""
    if not packet.deps:
        return None
    edges: dict[PacketId, set[PacketId]] = {}
    if dep_graph is not None:
        for e in dep_graph.edges:
            edges.setdefault(e.from_, set()).add(e.to)
    if existing_packets is not None:
        for pid, pkt in existing_packets.items():
            for d in pkt.deps:
                edges.setdefault(pid, set()).add(d)
    for dep in packet.deps:
        edges.setdefault(packet.id, set()).add(dep)
    for dep in packet.deps:
        if _dfs_reachable(dep, packet.id, edges):
            return Violation(
                code="DEPS_CYCLE",
                detail=f"添加 {packet.id!r} 会产生依赖环：从 {dep!r} 可走回 {packet.id!r}。",
                field="deps",
            )
    return None


def _dfs_reachable(
    start: PacketId,
    target: PacketId,
    edges: dict[PacketId, set[PacketId]],
    visited: set[PacketId] | None = None,
) -> bool:
    if visited is None:
        visited = set()
    if start in visited:
        return False
    visited.add(start)
    for neighbor in edges.get(start, ()):
        if neighbor == target:
            return True
        if _dfs_reachable(neighbor, target, edges, visited):
            return True
    return False


def check_role_exists(
    packet: WorkPacket,
    *,
    role_specs: Sequence[RoleSpec] | None = None,
    **_ctx: object,
) -> Violation | None:
    """校验 packet.role 和 acceptance.authoredBy 是否存在于 RoleSpec。"""
    if role_specs is None:
        return None
    role_ids = {s.id for s in role_specs}
    if packet.role not in role_ids:
        return Violation(
            code="ROLE_NOT_FOUND",
            detail=f"角色 {packet.role!r} 不在已加载的 RoleSpec 中。已加载：{sorted(role_ids)}。",
            field="role",
        )
    if packet.acceptance.authoredBy not in role_ids:
        return Violation(
            code="ROLE_AUTHOR_NOT_FOUND",
            detail=f"验收作者 {packet.acceptance.authoredBy!r} 不在 RoleSpec 中。",
            field="acceptance.authoredBy",
        )
    return None


def check_role_model_isolation(
    packet: WorkPacket,
    *,
    role_specs: Sequence[RoleSpec] | None = None,
    **_ctx: object,
) -> Violation | None:
    """模型隔离：coder != reviewer。

    B 的 RoleSpec.modelPolicy.mustDifferFrom 声明隔离约束。
    准入时校验 packet 的 routing.model 不与 mustDifferFrom 角色冲突。
    """
    if role_specs is None or packet.routing is None:
        return None
    spec = next((s for s in role_specs if s.id == packet.role), None)
    if spec is None or spec.modelPolicy is None:
        return None
    must_diff = spec.modelPolicy.mustDifferFrom or ()
    if not must_diff:
        return None
    packet_model = packet.routing.model
    for other_role_id in must_diff:
        other_spec = next((s for s in role_specs if s.id == other_role_id), None)
        if other_spec is None or other_spec.modelPolicy is None:
            continue
        other_model = other_spec.modelPolicy.defaultModel
        if other_model and packet_model == other_model:
            return Violation(
                code="MODEL_ISOLATION",
                detail=(
                    f"角色 {packet.role!r} 的模型 {packet_model!r} 与 "
                    f"{other_role_id!r} 的默认模型相同。"
                    f"同一模型既写又审 → 盲区重叠 → 评审失效。"
                ),
                field="routing.model",
            )
    return None


DEFAULT_RULES: tuple[RuleFn, ...] = (
    check_self_review,
    check_owns_paths,
    check_budget_limit,
    check_budget_degradation,
    check_deps_no_self_ref,
    check_deps_dag,
    check_role_exists,
    check_role_model_isolation,
)
