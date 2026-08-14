"""AdmissionChecker —— 准入校验器。

在 WorkPacket 进入系统前执行全部校验规则。
所有规则通过 → 准入。任一违规 → 拒绝并给出结构化理由。

确定性，零 LLM。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from codentum_contracts.state import (
    DependencyGraph,
    PacketId,
    RoleSpec,
    WorkPacket,
)

from .rules import DEFAULT_RULES, JUDGEMENT_MODES, RuleFn, Violation

__all__ = ["AdmissionChecker", "AdmissionVerdict"]


@dataclass(frozen=True, slots=True)
class AdmissionVerdict:
    """准入判定的结果。

    allowed=True  → 全部规则通过，可以进入 pending。
    allowed=False → violations 列出所有违规。
    """

    allowed: bool
    violations: tuple[Violation, ...] = ()

    def __bool__(self) -> bool:
        return self.allowed


# ★ RuleFn 定义在 rules.py，这里只引用 —— 同一个签名写两份，改一处忘一处就漂移。


@dataclass
class AdmissionChecker:
    """准入校验器。

    用法：
        checker = AdmissionChecker(role_specs=specs)
        verdict = checker.check(packet)
        if not verdict:
            for v in verdict.violations:
                print(f"  {v.code}: {v.detail}")

    可注入自定义规则：
        checker = AdmissionChecker(rules=(my_rule, *DEFAULT_RULES))
    """

    rules: Sequence[RuleFn] = field(default=DEFAULT_RULES)
    """校验规则列表。按顺序执行，第一条违规后不短路——收集全部违规再返回。"""

    role_specs: tuple[RoleSpec, ...] | None = None
    """已加载的 RoleSpec。为 None 时跳过角色相关校验。"""

    existing_packets: dict[PacketId, WorkPacket] | None = None
    """系统中已有的 packet（用于依赖 DAG 检查）。"""

    dep_graph: DependencyGraph | None = None
    """依赖图（用于 DAG 检查的另一个数据源）。"""

    modes: Mapping[str, str] = field(default_factory=lambda: dict(JUDGEMENT_MODES))
    """判据 → 档位。**没有登记的一律 enforcing**（见 rules.JUDGEMENT_MODES）。"""

    recorder: Callable[[str, str, str, bool, str | None], None] | None = None
    """命中记录回调 `(packet_id, rule_name, mode, fired, code)`。为 None 时不记录。

    ★ packet_id 是必须的：缺口报告要回答「这个 packet 失败之前，
      有没有任何判据 fired 过」—— 没有它，命中记录与失败记录**关联不起来**，
      而那正是「判据缺口」的定义所在。

    ★ 为什么是注入而不是在这里直接写文件：控制平面的承诺是
      「确定性代码，零 LLM，不猜路径」。往哪写是装配点的决定 ——
      让控制平面自己挑一个目录，就会和引擎的 state_dir 分叉成两处，
      而两处各自看起来都正常工作。

    ★ 为 None = 不记录，且这件事是**看得见的**：
      资产负债表会把「从未有过命中记录」与「命中 0 次」分开显示。
      两者完全不同 —— 前者是没人在记，后者是真的没命中。
    """

    def check(self, packet: WorkPacket) -> AdmissionVerdict:
        """对单个 WorkPacket 执行全部准入规则。

        返回 AdmissionVerdict：allowed=True 表示可以准入。
        """
        violations: list[Violation] = []

        ctx: dict[str, Any] = {
            "role_specs": self.role_specs,
            "existing_packets": self.existing_packets,
            "dep_graph": self.dep_graph,
        }

        for rule in self.rules:
            try:
                v = rule(packet, **ctx)
                mode = self.modes.get(rule.__name__, "enforcing")
                if self.recorder is not None:
                    self.recorder(str(packet.id), rule.__name__, mode, v is not None, v.code if v else None)
                # ★ shadow：评估了、记录了，**但不拦**。
                #   一条新判据在没有真实数据支撑之前，你不知道它会不会误伤；
                #   而第一次误拦就足以让人把它整条关掉，之后再也不会打开。
                if v is not None and mode != "shadow":
                    violations.append(v)
            except Exception as exc:
                # 规则本身出错不应阻断整个校验——
                # 但应记录为违规，因为这说明数据触发了规则的边界 case
                violations.append(
                    Violation(
                        code="I2_SELF_REVIEW",  # fallback
                        detail=f"规则 {rule.__name__} 执行异常: {exc}",
                        field=None,
                    )
                )

        if violations:
            return AdmissionVerdict(allowed=False, violations=tuple(violations))
        return AdmissionVerdict(allowed=True)