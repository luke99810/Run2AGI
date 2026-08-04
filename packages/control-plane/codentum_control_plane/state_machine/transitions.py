"""WorkPacket 状态机 —— 转换表从 RoleSpec 派生，不在这里硬编码。

════════════════════════════════════════════════════════════════
 这个模块最重要的一条设计
════════════════════════════════════════════════════════════════

`state.py` 里 PacketState 的注释写死了这句：

    合法转换由 RoleSpec 派生的转换表定义，**不在此处硬编码**。

而 `rolespec.schema.json` 的描述里也写着：

    ★ RoleSpec 是 Single Source，派生四处：工具面 / 卷挂载 / 状态转换表 /
      所有权注册 —— 任一处手工维护都会漂移，且漂移时通常不报错，
      **只是权限悄悄变宽**。

所以本模块**没有任何一张写死的合法转换表**。给它一组 RoleSpec，它算出表；
RoleSpec 变了表就跟着变。想知道"谁能把 packet 从 review 推到 accepted"，
答案只有一个地方能查，就是 RoleSpec。

★ 「权限悄悄变宽」是这类漂移的典型症状 —— 它不报错。所以本模块宁可在
  加载期就抛，也不在运行期放行一个说不清来源的转换。

════════════════════════════════════════════════════════════════
 强制的三条不变量
════════════════════════════════════════════════════════════════

  I6 证据      状态推进必须附证据引用，声明不算 → check() 要求 evidence 非空
  I2 验收可判定 进入 accepted 必须过 acceptance 门禁 → 由 RoleSpec 的
               requiresGate 表达，本模块负责把它透出来，不负责跑门禁
  —          guardian 不调模型 → schema 表达不了的条件约束，在加载期强制

★ 本模块**不执行门禁**，只回答「这次转换需不需要过门禁、过哪个」。
  跑门禁是 gates 模块的事。把判定和执行分开，是为了让本模块保持纯函数 ——
  纯函数才能被穷举测试。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from codentum_contracts.state import EvidenceRef, PacketState, RoleId, RoleSpec

__all__ = [
    "TerminalStateError",
    "TransitionDenied",
    "TransitionTable",
    "TransitionVerdict",
    "load_role_specs",
]


# ════════════════════════════════════════════════════════════════
#  终态 —— 唯一允许写死的常量，且理由必须成立
# ════════════════════════════════════════════════════════════════

TERMINAL_STATES: frozenset[PacketState] = frozenset({"accepted", "abandoned"})
"""终态：不允许再转出。

★ 这是本模块唯一写死的东西，因为它不是「策略」而是「语义」——
  accepted 表示已合入 main（I4 绿线已在其上重跑过），abandoned 表示已放弃。
  两者都已经从调度视野里移除，再转出意味着状态与现实脱节。

★ 注意 `rejected` **不是**终态：打回重做是正常路径，
  rejected → ready 必须允许，否则一次评审不通过就等于任务死亡。
  这是最容易被顺手写进终态集的一个 —— 写进去不报错，只是所有被打回的
  packet 都悄悄卡死。
"""


DenyReason = Literal[
    "unknown_transition",
    "role_not_permitted",
    "terminal_state",
    "missing_evidence",
    "same_state",
]


@dataclass(frozen=True, slots=True)
class TransitionVerdict:
    """一次转换判定的结果。

    ★ 拒绝是**预期内**的返回值，不是异常 —— 调度器每一轮都会撞到它。
      用异常表达会让正常路径穿过 except 分支。
    """

    allowed: bool
    reason: DenyReason | None = None
    requires_gate: str | None = None
    """需要过的门禁 id。allowed=True 且此项非 None → 调用方必须先跑门禁再落状态。"""
    detail: str = ""

    def __bool__(self) -> bool:
        return self.allowed


class TransitionDenied(Exception):
    """当调用方选择用异常风格时（`assert_allowed`）抛出。"""

    def __init__(self, verdict: TransitionVerdict) -> None:
        super().__init__(verdict.detail or str(verdict.reason))
        self.verdict = verdict


class TerminalStateError(ValueError):
    """RoleSpec 声明了一条从终态转出的转换 —— 加载期就拒绝。"""


# ════════════════════════════════════════════════════════════════
#  加载 RoleSpec：强制 schema 表达不了的那些约束
# ════════════════════════════════════════════════════════════════


def load_role_specs(specs: Iterable[RoleSpec]) -> tuple[RoleSpec, ...]:
    """校验一组 RoleSpec 的跨字段约束，通过则原样返回。

    ★ 这里检查的每一条，都是 **JSON Schema 表达不了**、因而 schema 过了
      也不代表被检查过的东西。rolespec.schema.json 自己就写着这句警告：
      「不要因为 schema 过了就以为它被检查过」。
    """
    loaded = tuple(specs)

    seen: set[RoleId] = set()
    for spec in loaded:
        if spec.id in seen:
            raise ValueError(f"RoleSpec 重复定义：{spec.id}。转换表会取哪一份是未定义行为")
        seen.add(spec.id)

        # ── guardian 不调模型 ──────────────────────────────────
        # schema 的原话：「JSON Schema 表达不了『id=guardian 时 usesModel 必须
        # 为 false』这种条件约束，由加载 RoleSpec 的代码强制」。这里就是那段代码。
        #
        # ★ 为什么这条值得单独强制：guardian 是六条不变量的守门人。
        #   守门人一旦开始"判断"而不是"检查"，整套确定性论证就塌了 ——
        #   而且塌得无声无息，因为它大部分时候会判对。
        if spec.id == "guardian" and spec.usesModel:
            raise ValueError(
                "guardian.usesModel 必须为 false。"
                "★ 确定性拦截器不碰模型 —— 它是六条不变量的守门人，"
                "让它调模型等于把最后一道确定性防线变成概率性的。"
            )

        # ── 不得从终态转出 ────────────────────────────────────
        for t in spec.transitions:
            if t.from_ in TERMINAL_STATES:
                raise TerminalStateError(
                    f"RoleSpec[{spec.id}] 声明了从终态 {t.from_!r} 转出到 {t.to!r} 的转换。"
                    f"★ 终态意味着已经离开调度视野（accepted 已合入 main，abandoned 已放弃），"
                    f"再转出会让状态与现实脱节。"
                )
            if t.from_ == t.to:
                raise ValueError(
                    f"RoleSpec[{spec.id}] 声明了自环转换 {t.from_!r} → {t.to!r}。"
                    f"★ 自环在状态机里没有语义，但它会让「状态推进必须附证据」(I6) 出现漏洞："
                    f"反复原地转换即可不断产生看似合法的状态变更记录。"
                )

    return loaded


# ════════════════════════════════════════════════════════════════
#  转换表
# ════════════════════════════════════════════════════════════════

_Key = tuple[RoleId, PacketState, PacketState]


class TransitionTable:
    """从一组 RoleSpec 派生出的状态转换表。

    不可变：构造后不提供任何修改入口。想改转换，改 RoleSpec 再重建 ——
    否则运行时的表和磁盘上的 RoleSpec 会分叉，而那正是「派生四处、
    任一处手工维护都会漂移」警告的那件事。
    """

    def __init__(self, specs: Iterable[RoleSpec]) -> None:
        loaded = load_role_specs(specs)
        table: dict[_Key, str | None] = {}
        for spec in loaded:
            for t in spec.transitions:
                key: _Key = (spec.id, t.from_, t.to)
                previous = table.get(key, _UNSET)
                if previous is not _UNSET and previous != t.requiresGate:
                    # 同一角色对同一转换声明了两个不同的门禁 —— 取哪个都是猜。
                    raise ValueError(
                        f"RoleSpec[{spec.id}] 对 {t.from_} → {t.to} 声明了两个不同的门禁："
                        f"{previous!r} 与 {t.requiresGate!r}。★ 二选一的默认行为一定是错的，"
                        f"所以这里不选，直接拒绝加载。"
                    )
                table[key] = t.requiresGate
        self._table: Mapping[_Key, str | None] = table
        self._roles: frozenset[RoleId] = frozenset(s.id for s in loaded)

    # ── 查询 ─────────────────────────────────────────────────

    def check(
        self,
        *,
        role: RoleId,
        current: PacketState,
        target: PacketState,
        evidence: Sequence[EvidenceRef] = (),
    ) -> TransitionVerdict:
        """判定一次状态推进是否允许。**纯函数，不产生任何副作用。**

        ★ evidence 是 I6 的强制点：「状态推进必须附证据引用，声明不算」。
          缺证据直接拒，不是警告 —— 一个能在没有证据的情况下推进的状态机，
          等于允许 Agent 自我宣布完成。
        """
        if current == target:
            return TransitionVerdict(
                allowed=False,
                reason="same_state",
                detail=f"{current} → {target}：源状态与目标状态相同，不是一次推进",
            )

        if current in TERMINAL_STATES:
            return TransitionVerdict(
                allowed=False,
                reason="terminal_state",
                detail=(
                    f"{current!r} 是终态，不允许转出。"
                    f"★ 若确实需要重开，应新建 packet 并在溯源图上标明 parent，"
                    f"而不是把终态改回去 —— 后者会让历史失真。"
                ),
            )

        if role not in self._roles:
            return TransitionVerdict(
                allowed=False,
                reason="role_not_permitted",
                detail=f"角色 {role!r} 没有加载任何 RoleSpec，因而不持有任何转换权限",
            )

        key: _Key = (role, current, target)
        if key not in self._table:
            holders = self.roles_allowing(current, target)
            hint = f"该转换属于 {sorted(holders)}" if holders else "没有任何角色声明过该转换"
            return TransitionVerdict(
                allowed=False,
                reason="unknown_transition",
                detail=f"角色 {role!r} 不能执行 {current} → {target}。{hint}",
            )

        if not evidence:
            return TransitionVerdict(
                allowed=False,
                reason="missing_evidence",
                requires_gate=self._table[key],
                detail=(
                    f"{current} → {target} 缺少证据引用。"
                    f"★ I6：状态推进必须附证据，声明不算 —— 没有证据的成功不算成功。"
                ),
            )

        return TransitionVerdict(allowed=True, requires_gate=self._table[key])

    def assert_allowed(
        self,
        *,
        role: RoleId,
        current: PacketState,
        target: PacketState,
        evidence: Sequence[EvidenceRef] = (),
    ) -> str | None:
        """check 的异常风格版本。返回需要过的门禁 id（可能为 None）。"""
        verdict = self.check(role=role, current=current, target=target, evidence=evidence)
        if not verdict.allowed:
            raise TransitionDenied(verdict)
        return verdict.requires_gate

    def roles_allowing(self, current: PacketState, target: PacketState) -> frozenset[RoleId]:
        """哪些角色能执行这条转换。空集合表示没有任何角色能 —— 那多半是 RoleSpec 漏了。"""
        return frozenset(r for (r, f, t) in self._table if f == current and t == target)

    def targets_from(self, role: RoleId, current: PacketState) -> frozenset[PacketState]:
        """某角色在某状态下能推到哪些状态。"""
        return frozenset(t for (r, f, t) in self._table if r == role and f == current)

    def reachable_states(self) -> frozenset[PacketState]:
        """转换表覆盖到的所有状态（源 + 目标）。用于 §检查覆盖度。"""
        states: set[PacketState] = set()
        for _r, f, t in self._table:
            states.add(f)
            states.add(t)
        return frozenset(states)

    def unreachable_states(self, all_states: Iterable[PacketState]) -> frozenset[PacketState]:
        """声明存在、但没有任何转换能进入的状态。

        ★ 这个查询存在的理由：一个进不去的状态不会报错，它只是永远为空。
          而"永远为空的状态"和"这条路径从没被走到过"在看板上长得一模一样。
          启动时跑一次，把它变成显式告警而不是无声的空列。
        """
        entered = {t for (_r, _f, t) in self._table}
        return frozenset(s for s in all_states if s not in entered)

    def __len__(self) -> int:
        return len(self._table)


class _Unset:
    __slots__ = ()


_UNSET = _Unset()
