"""经验召回：把晋级过的操作约束送回执行者的上下文。

════════════════════════════════════════════════════════════════
 ★ 为什么经验不能和知识资源共用一次检索
════════════════════════════════════════════════════════════════

现有的 `memory_context_candidates_now` 用**需求文本**做词法检索，
对「用户提供的领域知识」是对的 —— 做订阅页面就该召回订阅相关的资料。

但对进化层沉淀的经验，这个检索键是错的：

    经验：「run_tests 在 cwd=workspace/ 下会 ImportError: No module named 'src'」
    需求：「实现订阅费用页面」
    词法重叠：0 → 被 `_rank` 丢掉

★ **经验的相关性来自「你是这个角色」，不是「这次要做什么」。**
  一条操作约束对这个角色的每一个任务都成立，恰恰因为它和任务内容无关。

如果不分开，后果是静默的：写入侧 L0/L1 越攒越多，读取侧一条都出不来，
从外面看就是「记忆系统在跑，只是好像没什么用」。

════════════════════════════════════════════════════════════════
 ★ 用 EXACT 模式，不是「关掉过滤」
════════════════════════════════════════════════════════════════

契约里写着检索有确定性梯度、配方应优先用靠上的档位。
按 scope key 精确取正好落在最确定的那一档 —— 它不需要任何相关性猜测，
因此**同 query 同 index 必然逐条相同**，replay 可复现。

★ 只召回 L1 及以上：L0 是 packet 作用域的一次性观察，
  它还没有证据说明自己普遍成立。把 L0 也灌进去等于让
  「某个 packet 那次的偶然」影响之后所有执行 —— 晋级门槛就白设了。
"""

from __future__ import annotations

from codentum_contracts import PacketId, RetrievalMode, RetrievalQuery
from codentum_contracts.interfaces import MemoryScope
from codentum_contracts.state import RoleSpec

from codentum_harness.context_broker import ContextCandidate

from codentum_harness.memory_index import PersistentMemoryIndex

__all__ = ["experience_context_candidates_now"]


def experience_context_candidates_now(
    index: PersistentMemoryIndex,
    *,
    role_spec: RoleSpec,
    packet_id: PacketId,
    limit: int = 5,
    char_budget: int = 2000,
    priority: int = 5,
) -> tuple[ContextCandidate, ...]:
    """召回该角色晋级过的经验（L1+）。"""

    scope = MemoryScope(kind="role", role=role_spec.id)
    result = index.retrieve_now(
        RetrievalQuery(
            mode=RetrievalMode.EXACT,
            # ★ `_exact_keys` 里除了 ref 就是 scope key，所以按它取
            #   等价于「这个角色作用域下的全部条目」。
            q=f"role:{role_spec.id}",
            scope=scope,
            limit=limit,
            char_budget=char_budget,
            min_level="L1",
        )
    )

    candidates: list[ContextCandidate] = []
    for offset, entry in enumerate(result.entries):
        candidates.append(
            ContextCandidate(
                ref=f"memory:{entry.ref}",
                artifact_path=f".codentum/memory/experience/{packet_id}/{entry.ref.split(':')[-1]}.md",
                text=(
                    f"[{entry.level} 经验 · {role_spec.id}] {entry.text}\n"
                    f"indexVersion: {result.index_version}"
                ),
                summary=f"{entry.level} 经验：{entry.text[:60]}",
                priority=priority + offset,
            )
        )
    return tuple(candidates)
