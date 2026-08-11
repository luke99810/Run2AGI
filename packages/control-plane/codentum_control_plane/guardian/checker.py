"""Guardian —— 确定性拦截器。

六条不变量的运行时守门人。零 LLM，零网络。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from codentum_contracts.state import (
    PacketId,
    RoleId,
    RoleSpec,
    WorkPacket,
)

__all__ = ["Guardian", "Interception", "InterceptionCode"]


InterceptionCode = Literal[
    "I3_CONTRACT_FREEZE",
    "I5_MAX_ATTEMPTS",
    "I5_BUDGET_EXHAUSTED",
]


@dataclass(frozen=True, slots=True)
class Interception:
    """一次拦截记录。"""
    code: InterceptionCode
    packet_id: PacketId
    detail: str
    at: str = ""


DEFAULT_FROZEN_PATHS: tuple[str, ...] = (
    "packages/contracts/",
)

DEFAULT_MAX_ATTEMPTS = 3

ARCHITECT_ROLE: RoleId = "architect"


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Guardian:
    """确定性拦截器。I3 契约冻结 + I5 单会话闭合。"""

    frozen_paths: tuple[str, ...] = DEFAULT_FROZEN_PATHS
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    _interceptions: list[Interception] = field(default_factory=list, init=False, repr=False)
    _total_intercepted: int = field(default=0, init=False, repr=False)

    # ── I3 契约冻结 ──

    def check_path_write(
        self, packet: WorkPacket, path: str,
        *, role_specs: Sequence[RoleSpec] | None = None,
    ) -> tuple[bool, str]:
        """检查 packet 的角色是否有权写入 path。
        命中 frozen_paths 且 role != architect → 拦截。
        """
        normalized = path.replace("\\", "/").rstrip("/")

        for frozen in self.frozen_paths:
            fn = frozen.replace("\\", "/").rstrip("/")
            if normalized == fn or normalized.startswith(fn + "/"):
                if packet.role != ARCHITECT_ROLE:
                    msg = f"I3: {packet.role} 试图写入受保护路径 {path}。只有 architect 可以写 {frozen}。"
                    self._interceptions.append(Interception(
                        code="I3_CONTRACT_FREEZE", packet_id=packet.id,
                        detail=msg, at=_now_iso(),
                    ))
                    return False, msg
                return True, ""

        if role_specs is not None:
            spec = next((s for s in role_specs if s.id == packet.role), None)
            if spec is not None and not _path_matches_any(normalized, spec.writes):
                msg = f"角色 {packet.role} 不允许写入 {path}。允许: {spec.writes}。"
                self._interceptions.append(Interception(
                    code="I3_CONTRACT_FREEZE", packet_id=packet.id,
                    detail=msg, at=_now_iso(),
                ))
                return False, msg

        return True, ""

    # ── I5 单会话闭合 ──

    def check_attempts(self, packet: WorkPacket) -> tuple[bool, str]:
        """超过 max_attempts → 拦截。"""
        if packet.attempts >= self.max_attempts:
            msg = f"I5: {packet.id} 已尝试 {packet.attempts} 次，超过上限 {self.max_attempts}。"
            self._interceptions.append(Interception(
                code="I5_MAX_ATTEMPTS", packet_id=packet.id,
                detail=msg, at=_now_iso(),
            ))
            return False, msg
        return True, ""

    def check_budget(self, packet: WorkPacket, remaining: float) -> tuple[bool, str]:
        """检查全局预算是否已耗尽。"""
        if remaining <= 0:
            msg = f"I5: 预算耗尽，剩余 ${remaining:.2f}，无法为 {packet.id} 分配资源。"
            self._interceptions.append(Interception(
                code="I5_BUDGET_EXHAUSTED", packet_id=packet.id,
                detail=msg, at=_now_iso(),
            ))
            return False, msg
        return True, ""

    # ── 拦截记录 ──

    @property
    def interceptions(self) -> tuple[Interception, ...]:
        return tuple(self._interceptions)

    def drain_interceptions(self) -> tuple[Interception, ...]:
        result = tuple(self._interceptions)
        self._total_intercepted += len(result)
        self._interceptions.clear()
        return result

    @property
    def interception_count(self) -> int:
        return len(self._interceptions)


def _path_matches_any(target: str, patterns: tuple[str, ...]) -> bool:
    """检查 target 是否匹配任一 glob pattern。支持 ** 前缀匹配。"""
    for pat in patterns:
        pn = pat.replace("\\", "/").rstrip("/")
        if pn.endswith("/**"):
            prefix = pn[:-3]
            if target == prefix or target.startswith(prefix + "/"):
                return True
        elif pn == target or target.startswith(pn + "/"):
            return True
    return False