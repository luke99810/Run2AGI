"""GateRunner —— 门禁执行器。

管理具名门禁的注册与执行。
每个门禁是一个 (WorkPacket, **context) -> GateVerdict 的函数。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from codentum_contracts.state import EvidenceRef, WorkPacket

__all__ = ["GateRunner", "GateVerdict", "GateFn"]


@dataclass(frozen=True, slots=True)
class GateVerdict:
    """一次门禁判定的结果。"""

    passed: bool
    gate_id: str
    detail: str
    evidence_refs: tuple[EvidenceRef, ...] = ()

    def __bool__(self) -> bool:
        return self.passed


GateFn = Callable[..., GateVerdict]
"""门禁函数签名：接收 WorkPacket + 关键字语境，返回 GateVerdict。"""


class GateRunner:
    """门禁执行器。

    用法：
        runner = GateRunner()
        runner.register("self-test", my_self_test_gate)
        verdict = runner.check("self-test", packet)
        if verdict.passed:
            ...
    """

    def __init__(
        self,
        recorder: Callable[[str, str, str, bool, str | None], None] | None = None,
    ) -> None:
        self._gates: dict[str, GateFn] = {}
        self.recorder = recorder
        """命中记录回调 `(packet_id, gate_id, mode, fired, code)`，为 None 时不记录。

        ★ 与 AdmissionChecker 的 recorder 同一个签名、同一种语义 ——
           「没命中」也要记（跑过 N 次一次没拦 = 可能多余；从没记录 = 观测坏了）。
           gate 没有 shadow 档位，一律 `enforcing`。
        """

    def register(self, gate_id: str, fn: GateFn) -> None:
        """注册一个门禁。同名覆盖（后注册的覆盖先注册的）。"""
        self._gates[gate_id] = fn

    def check(
        self, gate_id: str, packet: WorkPacket, **ctx: object
    ) -> GateVerdict:
        """执行指定门禁。

        若门禁未注册，返回 passed=True（未注册的门禁视为"不需要"而非"失败"）。
        这是刻意的：P0 阶段并非所有门禁都已实现，未实现的不应阻断流程。
        """
        fn = self._gates.get(gate_id)
        if fn is None:
            return GateVerdict(
                passed=True,
                gate_id=gate_id,
                detail=f"门禁 {gate_id!r} 未注册，默认放行（P0 阶段允许未实现的门禁不阻断）",
            )

        try:
            verdict = fn(packet, **ctx)
        except Exception as exc:
            verdict = GateVerdict(
                passed=False,
                gate_id=gate_id,
                detail=f"门禁 {gate_id!r} 执行异常: {exc}",
            )

        # ★ 命中落账（与 AdmissionChecker 同签名）。未注册的门禁不记录 ——
        #   那是「不需要」，不是「跑过一次判据」。
        if self.recorder is not None:
            self.recorder(
                str(packet.id),
                gate_id,
                "enforcing",
                not verdict.passed,
                gate_id if not verdict.passed else None,
            )
        return verdict

    def has(self, gate_id: str) -> bool:
        """检查门禁是否已注册。"""
        return gate_id in self._gates

    @property
    def registered(self) -> tuple[str, ...]:
        """所有已注册的门禁 ID。"""
        return tuple(sorted(self._gates))