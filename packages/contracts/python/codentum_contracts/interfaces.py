"""四个核心接口 —— 五个平面之间【唯一】的通道。

    WorkerRuntime   控制平面 ↔ 执行平面
    ArtifactStore   执行平面 ↔ 数据平面
    MemoryIndex     上下文平面 ↔ 数据平面
    ModelGateway    执行平面 ↔ 模型供应商

★ 定死这四个签名，等于定死了平面之间怎么说话。
  其余一切都是各自平面的内部实现，可以随便重写。

★ 这四个接口【只有 Python 一侧】——它们全在引擎内部。
  桌面端是纯读端，只读 .codentum/ 里的状态文件，不实现也不调用它们。
  所以跨语言边界只覆盖 state 数据，不覆盖行为契约，比预想的小得多。

本文件【手写】，不是生成物 —— 行为契约（方法签名）JSON Schema 表达不了。
数据形状在 state.py（生成物），两者的分工判据：
    它会不会出现在磁盘上的某个 JSON 里？会 → state.py；不会 → 这里。

★ 第 0 周已冻结。此后只允许加实现，不允许改签名；
  要改需三人同意 + 新 ADR + 走变更窗口。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, Protocol, runtime_checkable

from .state import (
    Effort,
    EvidenceRef,
    ModelId,
    ModelRouting,
    PacketId,
    RoleId,
)

# ══════════════════════════════════════════════════════════════
#  进程内传参的类型（不落盘，所以不进 schema）
# ══════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class MountSpec:
    """卷挂载。

    ★ mode="ro" 是「改测试」「改契约」两种作弊的物理堵法，不是建议。
      约束优先级：不可见 > 无权限 > 被拦截 > 提示词劝阻 —— 这里是第二档。
    """

    host_path: str
    mount_path: str
    mode: Literal["ro", "rw"]


@dataclass(frozen=True, slots=True)
class BudgetGrantRuntime:
    """本次执行获批的预算。★ 货币计，不用 token。

    不同模型家族的分词器差异可达 ~30%，异构路由下按 token 记预算会静默失真，
    且失真方向随路由变化，事后查不出来。
    """

    limit_usd: float
    degradation_chain: Sequence[str]


# ══════════════════════════════════════════════════════════════
#  WorkerRuntime —— 控制平面 ↔ 执行平面
# ══════════════════════════════════════════════════════════════
#
# ★ 这是两个平面之间【唯一】的通道。除此之外不许有任何直连
#   （不 import、不共享内存、不读对方的文件）。
#
# ── 三条设计约束，实现时不许绕 ──────────────────────────────
#
# 1. 控制平面不看任务内容。SpawnRequest 里只有元数据：packet 引用、挂载表、
#    工具面、模型路由、预算。★ 没有"要写什么代码"这种字段 ——
#    那些由 Harness 从 packet 和上下文里自己装配。
#
# 2. 这里没有 retry()。重试与升级是控制平面的决定，不是执行体的能力。
#    执行体只有「跑完 / 跑砸 / 被中止」三种结局，之后由控制平面裁决。
#    ★ 加一个 retry() 看起来方便，实际上会让执行体开始迎合调度逻辑。
#
# 3. WorkerOutcome 必须带 evidence。没有证据的成功不算成功（I6）。
#    类型上设为必填，就是为了让"忘了写证据"在静态检查阶段就暴露。


@dataclass(frozen=True, slots=True)
class WorkerHandle:
    """一次执行的句柄。跨进程有效，可用于崩溃后重新接管。"""

    worker_id: str
    packet_id: PacketId
    role: RoleId
    runtime_ref: str


@dataclass(frozen=True, slots=True)
class SpawnRequest:
    packet_id: PacketId
    role: RoleId
    mounts: Sequence[MountSpec]
    """卷挂载表。★ 只读/可写在这里定死，进模型前物理生效。"""

    tools: Sequence[str]
    """工具面白名单。★ 未列出的工具【不出现在工具列表里】，不是调用时被拒。"""

    routing: ModelRouting
    budget: BudgetGrantRuntime
    workspace: str
    """隔离工作区的位置（git worktree 路径）。"""

    attempt: int
    """第几次尝试，从 1 开始。★ 执行体只读，用于日志；不得据此改变行为。"""


class FailureCode(StrEnum):
    """★ 机器可读的失败理由码，不是给人看的文本。

    控制平面据此决定重试还是升级 —— 靠解析自然语言做调度决策，
    等于把确定性控制平面的一部分交回给了字符串匹配。
    """

    ACCEPTANCE_NOT_MET = "acceptance_not_met"
    BUDGET_EXHAUSTED = "budget_exhausted"
    TOOL_DENIED = "tool_denied"
    LOCK_LOST = "lock_lost"
    CONTRACT_VIOLATION = "contract_violation"
    RUNTIME_ERROR = "runtime_error"
    MODEL_ERROR = "model_error"
    TIMEOUT = "timeout"


class AbortReason(StrEnum):
    PREEMPTED = "preempted"
    OPERATOR = "operator"
    INVARIANT_BREACH = "invariant_breach"
    SHUTDOWN = "shutdown"


@dataclass(frozen=True, slots=True)
class CheckpointRef:
    worker_id: str
    seq: int
    digest: str


@dataclass(frozen=True, slots=True)
class WorkerCompleted:
    evidence: Sequence[EvidenceRef]
    """★ 必填。没有证据的成功不算成功（I6）。"""

    spent_usd: float
    touched_paths: Sequence[str]
    status: Literal["completed"] = "completed"


@dataclass(frozen=True, slots=True)
class WorkerFailed:
    reason_code: FailureCode
    detail: str
    evidence: Sequence[EvidenceRef]
    spent_usd: float
    status: Literal["failed"] = "failed"


@dataclass(frozen=True, slots=True)
class WorkerAborted:
    reason: AbortReason
    spent_usd: float
    status: Literal["aborted"] = "aborted"


WorkerOutcome = WorkerCompleted | WorkerFailed | WorkerAborted
"""执行体的终局。只有这三种 —— 判定"算不算通过"是门禁的事，不是这里。"""


@dataclass(frozen=True, slots=True)
class WorkerEvent:
    kind: Literal[
        "started", "tool_call", "tool_blocked", "progress", "checkpoint", "cost", "finished"
    ]
    at: str
    seq: int
    payload: Mapping[str, Any] = ...  # type: ignore[assignment]  # dataclass 默认值占位，实现时必填


@runtime_checkable
class WorkerRuntime(Protocol):
    async def spawn(self, req: SpawnRequest) -> WorkerHandle:
        """启动一次执行，立即返回句柄，不等待完成。"""
        ...

    def events(self, handle: WorkerHandle, since_seq: int = 0) -> AsyncIterator[WorkerEvent]:
        """订阅事件流。断开重连后应能从 since_seq 续上。"""
        ...

    async def settle(self, handle: WorkerHandle) -> WorkerOutcome:
        """等待终局。"""
        ...

    async def abort(self, handle: WorkerHandle, reason: AbortReason) -> None: ...

    async def resume(self, ref: CheckpointRef) -> WorkerHandle:
        """★ 崩溃恢复用。用户会关电脑，这不是可选项。"""
        ...

    async def adopt(self, runtime_ref: str) -> WorkerHandle | None:
        """重启后重新接管仍在跑的执行体。"""
        ...


# ══════════════════════════════════════════════════════════════
#  ArtifactStore —— 执行平面 ↔ 数据平面
# ══════════════════════════════════════════════════════════════
#
# ── 两条设计约束 ──────────────────────────────────────────
#
# 1. ★ 内容寻址（ref 由内容摘要决定）。同样的内容存两次得到同一个 ref。
#    这让溯源图可信：ref 变了就一定是内容变了，不可能"同名不同物"。
#
# 2. ★ 没有 delete()，没有 update()。只追加。
#    审计哈希链和溯源图都建立在"存进去的东西不会变"之上 ——
#    开一个删除接口，整条链的可信度就没了。
#    真要清理，是运维层面的独立操作，不走这个接口。

ArtifactRef = str
"""内容寻址引用，格式 `<algo>:<hex>`。"""


class ArtifactKind(StrEnum):
    DIFF = "diff"
    TEST_REPORT = "test_report"
    REVIEW = "review"
    LOG = "log"
    SNAPSHOT = "snapshot"
    BUILD = "build"
    DOC = "doc"


@dataclass(frozen=True, slots=True)
class ArtifactMeta:
    ref: ArtifactRef
    kind: ArtifactKind
    size_bytes: int
    media_type: str
    created_at: str
    produced_by_packet: PacketId
    produced_by_role: RoleId
    derived_from: Sequence[ArtifactRef]
    """★ 溯源图靠它自动构建，不用手工登记。"""


@dataclass(frozen=True, slots=True)
class Artifact:
    meta: ArtifactMeta
    content: bytes


@dataclass(frozen=True, slots=True)
class ArtifactFilter:
    kind: ArtifactKind | None = None
    packet_id: PacketId | None = None
    role: RoleId | None = None
    since: str | None = None


@runtime_checkable
class ArtifactStore(Protocol):
    async def put(
        self,
        kind: ArtifactKind,
        content: bytes,
        *,
        produced_by_packet: PacketId,
        produced_by_role: RoleId,
        media_type: str,
        derived_from: Sequence[ArtifactRef] = (),
    ) -> ArtifactRef:
        """存入。内容寻址 —— 同内容返回同 ref，重复存入是幂等的。

        ★ 幂等是必须的：崩溃恢复时会重放，非幂等就会产生重复节点。
        """
        ...

    async def get(self, ref: ArtifactRef) -> Artifact: ...

    async def head(self, ref: ArtifactRef) -> ArtifactMeta:
        """只取元数据。不该为了看一眼大小就把几十 MB 读进内存。"""
        ...

    async def exists(self, ref: ArtifactRef) -> bool: ...

    def list(self, filter: ArtifactFilter) -> AsyncIterator[ArtifactMeta]: ...


# ══════════════════════════════════════════════════════════════
#  MemoryIndex —— 上下文平面 ↔ 数据平面
# ══════════════════════════════════════════════════════════════
#
# ── 三条设计约束 ──────────────────────────────────────────
#
# 1. ★★ 检索必须确定性可复现：同 query + 同 index_version → 逐条相同的结果。
#    这是 replay 能不能用的前提。检索不确定 → 上下文不可复现 →
#    出了问题永远查不出是模型的锅还是检索的锅。
#    所以实现里不许用"随机采样""按当前时间加权"这类非确定性手段。
#
# 2. ★ 检索有确定性梯度：exact 最确定、semantic 最不确定。
#    上下文配方应优先用高确定性档位，语义检索是兜底不是首选。
#
# 3. ★ 晋升是单向的（L0 → L4），且必须过证伪门。
#    这里只提供 promote()，不提供 demote() ——
#    退级是 Evolver 通过写一条 supersedes 边来表达的，不是把记忆改回去。

MemoryRef = str

MemoryLevel = Literal["L0", "L1", "L2", "L3", "L4"]
"""经验晋升五级：L0 一次观察 → L1 重复出现 → L2 归纳成假说 → L3 过证伪门 → L4 固化为规则。"""


class RetrievalMode(StrEnum):
    """★ 确定性梯度：从上到下确定性递减。配方应优先用靠上的。"""

    EXACT = "exact"
    """精确键查找 —— 完全确定"""
    STRUCTURAL = "structural"
    """沿图的边走（依赖/溯源）—— 确定"""
    LEXICAL = "lexical"
    """关键词 —— 确定（同 index 同结果）"""
    SEMANTIC = "semantic"
    """向量相似 —— ★ 最不确定，兜底用"""


@dataclass(frozen=True, slots=True)
class MemoryScope:
    kind: Literal["global", "role", "packet"]
    role: RoleId | None = None
    packet_id: PacketId | None = None


@dataclass(frozen=True, slots=True)
class RetrievalQuery:
    mode: RetrievalMode
    q: str
    scope: MemoryScope
    limit: int
    char_budget: int
    """本次检索允许占用的上下文预算（字符数）。超出由降级链处理，不是硬截断。"""

    min_level: MemoryLevel | None = None


@dataclass(frozen=True, slots=True)
class MemoryEntry:
    ref: MemoryRef
    level: MemoryLevel
    scope: MemoryScope
    text: str
    created_at: str
    hits: int = 0
    helpful: int = 0
    """命中后是否真起了作用。★ 回流给进化层 —— 没有它，进化层是瞎的。"""


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    entries: Sequence[MemoryEntry]
    index_version: str
    """★ 必须回传。replay 时用同一个 version 才能重建同样的上下文。"""

    degraded: bool
    """是否因预算不足降过级。降级了要留痕，否则上下文缺失会被误判成模型能力问题。"""


@dataclass(frozen=True, slots=True)
class PromotionJustification:
    kind: Literal["observation", "falsification_gate", "operator"]
    detail: str
    refs: Sequence[str]


@runtime_checkable
class MemoryIndex(Protocol):
    async def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        """★ 确定性：同 query + 同 index_version 必须返回逐条相同的结果。"""
        ...

    async def write(self, entry: MemoryEntry) -> MemoryRef: ...

    async def promote(
        self, ref: MemoryRef, to: MemoryLevel, justification: PromotionJustification
    ) -> None:
        """晋升一级。★ 单向，且 L2 → L3 必须附证伪门的结论。"""
        ...

    async def record_hit(self, ref: MemoryRef, helpful: bool) -> None:
        """记录命中效果，回流给进化层。"""
        ...

    async def version(self) -> str:
        """当前索引版本。装配上下文时记下它，replay 时校验。"""
        ...


# ══════════════════════════════════════════════════════════════
#  ModelGateway —— 执行平面 ↔ 模型供应商
# ══════════════════════════════════════════════════════════════
#
# 所有模型调用【必须】经过这里。绕过它直接调 SDK，
# 成本记账和模型隔离就都失效了。
#
# ── 四条设计约束，每条都对应一个踩过的坑 ─────────────────────
#
# 1. ★★ 预算一律用【货币】，不用 token。
#    不同模型家族的分词器差异可达 ~30%。异构路由下按 token 记预算会静默失真，
#    而且失真方向随路由变化 —— 查都没法查。所以本节【没有】任何
#    token_budget / max_tokens 之类的预算字段，只有 usd。
#
# 2. ★★ 同一次尝试内不许换模型。
#    提示词缓存是按模型分桶的，中途换模型 = 整个前缀冷重写，
#    既贵又慢，还会让"失败即升档"这种直觉做法变成净亏。
#    需要加强时【提高 effort】—— effort 变化不会让缓存失效。
#    类型上的体现：模型绑在 ModelSession 上，一经 open 不可变；
#    effort 可以每次调用都不同。
#
# 3. ★ 模型隔离是硬约束，由 open() 校验：coder ≠ reviewer，evolver ≠ verifier。
#    同一模型既写又审，盲区会在两处同时出现，评审就成了摆设。
#    违反直接【拒绝开会话】，而不是等调用时才发现。
#
# 4. ★ 延迟不敏感的角色（Evolver）走 Batch，约五折且无取舍。


@dataclass(frozen=True, slots=True)
class ToolSchema:
    name: str
    description: str
    input_schema: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ModelMessage:
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True, slots=True)
class ModelRequest:
    system: str
    messages: Sequence[ModelMessage]
    effort: Effort | None = None
    """★ 逐次可变。加强用它，不要换模型。"""

    tools: Sequence[ToolSchema] = ()
    cache_breakpoints: Sequence[int] = ()
    """缓存断点。放在稳定前缀之后，让重复部分命中缓存。"""


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    input: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Usage:
    """★ token 数在这里只作【可观测指标】存在，不作预算依据。

    预算判断一律看 cost_usd。
    """

    cost_usd: float
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int


@dataclass(frozen=True, slots=True)
class ModelResponse:
    text: str
    tool_calls: Sequence[ToolCall]
    stop_reason: Literal["end", "tool_use", "max_output", "refusal"]
    usage: Usage


@dataclass(frozen=True, slots=True)
class CostEstimate:
    estimated_usd: float
    upper_bound_usd: float
    """估算的不确定度。预算判定应按上界来，宁可高估。"""


@dataclass(frozen=True, slots=True)
class CostLedger:
    total_usd: float
    by_role: Mapping[str, float]
    by_model: Mapping[str, float]
    since: str


@runtime_checkable
class ModelSession(Protocol):
    """一次尝试 = 一个会话。

    ★ model 绑在会话上，开了就不能改。
      这不是接口洁癖 —— 它是在类型层面把"中途换模型"这条路堵死。
    """

    @property
    def session_id(self) -> str: ...

    @property
    def model(self) -> ModelId: ...

    @property
    def role(self) -> RoleId: ...

    async def invoke(self, req: ModelRequest) -> ModelResponse:
        """effort 可逐次调整（不影响缓存）；model 不行（会冷重写）。"""
        ...

    def stream(self, req: ModelRequest) -> AsyncIterator[Any]: ...

    def spent_usd(self) -> float:
        """本会话至今花了多少钱。★ 货币。"""
        ...

    async def close(self) -> None: ...


@runtime_checkable
class ModelGateway(Protocol):
    async def open(self, role: RoleId, routing: ModelRouting, grant_usd: float) -> ModelSession:
        """开一个会话。

        ★ 模型隔离在这里校验 —— 若 role 的 RoleSpec 声明了 must_differ_from，
          而 routing.model 与被隔离角色当前使用的模型相同，则【拒绝开会话】。
          不是警告，是抛错。
        """
        ...

    async def estimate(self, routing: ModelRouting, req: ModelRequest) -> CostEstimate:
        """预估花费，用于预算准入。★ 货币。"""
        ...

    async def ledger(self) -> CostLedger:
        """全局账本。桌面端的成本面板读它 —— ★ 显示货币，不显示 token。"""
        ...


__all__ = [
    "AbortReason",
    "Artifact",
    "ArtifactFilter",
    "ArtifactKind",
    "ArtifactMeta",
    "ArtifactRef",
    "ArtifactStore",
    "BudgetGrantRuntime",
    "CheckpointRef",
    "CostEstimate",
    "CostLedger",
    "FailureCode",
    "MemoryEntry",
    "MemoryIndex",
    "MemoryLevel",
    "MemoryRef",
    "MemoryScope",
    "ModelGateway",
    "ModelMessage",
    "ModelRequest",
    "ModelResponse",
    "ModelSession",
    "MountSpec",
    "PromotionJustification",
    "RetrievalMode",
    "RetrievalQuery",
    "RetrievalResult",
    "SpawnRequest",
    "ToolCall",
    "ToolSchema",
    "Usage",
    "WorkerAborted",
    "WorkerCompleted",
    "WorkerEvent",
    "WorkerFailed",
    "WorkerHandle",
    "WorkerOutcome",
    "WorkerRuntime",
]
