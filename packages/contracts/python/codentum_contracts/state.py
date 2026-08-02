"""状态数据类型（Pydantic 模型）

⚠️ 本文件由 `python scripts/gen_types.py` 从 packages/contracts/schemas/*.json 生成。
   【不要手改】—— 下次生成会覆盖。

要改数据形状：改 schema → 重跑生成 → 提交生成结果。
CI 里的 `gen:check` 会验证仓库内容与生成物一致。

★ 全部模型 frozen=True、extra="forbid"，数组用 tuple：
  状态是不可变的。四张图里只有所有权图需要并发控制，
  而"不可变 + 显式替换"让另外三张图连锁都不需要。

★★ 写回 .codentum/ 时必须用 dump_state()，不要直接 model_dump()。
   schema 里的 `from` 撞上 Python 关键字，属性名是 `from_`，靠 alias 映射回去。
   直接 model_dump() 会把 "from_" 写进 JSON —— 文件仍是合法 JSON，
   但 schema 校验会失败，而失败点离出错点很远。

真源：packages/contracts/schemas/*.json
生成器：scripts/gen_types.py
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal, NewType

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class _Base(BaseModel):
    """所有状态模型的基类。

    extra="forbid"      对应 schema 的 additionalProperties: false —— 多一个字段就报错。
    frozen=True         状态不可变，改动靠 model_copy(update=...) 显式产生新值。
    populate_by_name    允许用 Python 属性名构造（from_=...），也允许用 schema 名（from=...）。
    """

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


def dump_state(model: BaseModel) -> dict[str, Any]:
    """把模型序列化成【符合 schema】的 dict。写 .codentum/ 一律走这个。

    三个参数都是必须的，少一个就会写出不合 schema 的文件：

    ★ by_alias=True     把 from_ 还原成 from。
    ★ exclude_none=True schema 里可选字段的语义是"没有这个键"，不是"键存在但值为 null"
                        —— 这个区分正是 TS 侧 exactOptionalPropertyTypes 守的东西。
    ★ mode="json"       tuple → list。不加这个，dump 出来的是 Python 结构而非 JSON 结构，
                        json.dumps 能写出去，但任何逐字段比对/往返校验都会失败。
    """
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)


# ════════════════════════════════════════════════════════════
#  标识
# ════════════════════════════════════════════════════════════

PacketId = NewType("PacketId", Annotated[str, StringConstraints(pattern=r"^wp-[0-9a-z]{6,}$")])
"""WorkPacket 的全局唯一 id，创建后不变。"""


EvidenceRef = NewType("EvidenceRef", str)
"""证据引用。★ I6：状态推进必须附证据引用，声明不算。"""


ArtifactRefStr = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+:[0-9a-f]+$")]
"""内容寻址的产出物引用，格式 `<algo>:<hex>`。★ 内容寻址意味着 ref 变了就一定是内容变了，不可能同名不同物 —— 溯源图的可信度建立在这上面。"""


Digest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
"""
摘要，格式 `sha256:<64位小写十六进制>`。
★ 审计哈希链的链首用 sha256: 后跟 64 个 0 —— 哨兵值也必须是合法摘要格式，否则解析链首要走特例分支，而特例分支是最少被测到的分支。
"""


PacketState = Literal["pending", "ready", "running", "blocked", "review", "accepted", "rejected", "abandoned"]
"""WorkPacket 的状态。★ 定义在这里而不是 workpacket.schema.json —— RoleSpec 的 transitions 也要用它，两处各写一份 enum 就会漂移。合法转换由 RoleSpec 派生的转换表定义，不在此处硬编码。"""


RoleId = Literal["intake", "architect", "planner", "qa", "coder", "helper", "reviewer", "integrator", "manager", "evolver", "guardian"]
"""11 个角色。★ guardian 是唯一 usesModel = false 的 —— 确定性拦截器不碰模型。"""


ModelId = str
"""模型标识。"""


Effort = Literal["low", "medium", "high", "xhigh", "max"]
"""推理强度。★ 需要加强时调这个，不要换模型 —— 提示词缓存按模型分桶，同一次尝试内换模型 = 整个前缀冷重写。"""


Timestamp = str
"""ISO-8601 时间戳。"""


# ════════════════════════════════════════════════════════════
#  WorkPacket
# ════════════════════════════════════════════════════════════

class WorkPacket(_Base):
    """调度的最小单位。一个 WorkPacket = 一段可独占的写权限 + 一条可判定的验收 + 一份预算。对应 K8s 的 Pod spec。"""
    id: PacketId
    """WorkPacket 的全局唯一 id，创建后不变。"""
    kind: PacketKind
    """spike = 探索性任务（算法类），产出是结论不是代码；结论固化后另开 impl packet。"""
    state: PacketState
    """WorkPacket 的状态。★ 定义在这里而不是 workpacket.schema.json —— RoleSpec 的 transitions 也要用它，两处各写一份 enum 就会漂移。合法转换由 RoleSpec 派生的转换表定义，不在此处硬编码。"""
    role: RoleId
    """11 个角色。★ guardian 是唯一 usesModel = false 的 —— 确定性拦截器不碰模型。"""
    ownsPaths: tuple[str, ...]
    """
    ★ I1 单写者：运行期间独占写权限。与任何其他 running packet 的 ownsPaths 不得相交 —— 由 control-plane/locks 强制，不靠约定。生成物不进 ownsPaths。
    注意：pending 的 packet 可以声明与在跑的锁重叠的路径（如集成 packet 声明 src/），锁只对 running 生效。
    """
    readsPaths: tuple[str, ...]
    """
    只读挂载。★ 约束靠挂载权限物理生效，不靠提示词劝阻。
    对 coder 而言 tests/ 与 packages/contracts/ 必须在这里而不是 ownsPaths —— 这是「改测试」「改契约」两种作弊的堵法。
    """
    deps: tuple[PacketId, ...]
    """依赖图的边。必须是 DAG —— 成环由准入校验器拒绝。"""
    acceptance: Acceptance
    """★ I2 验收可判定。由 QA 在实现之前写，Coder 看不见也改不了 —— 这是三种作弊里「缩小范围」的堵法。"""
    budget: BudgetGrant
    """本 packet 的预算额度。"""
    routing: ModelRouting | None = None
    """留空则用角色默认（RoleSpec.modelPolicy）。"""
    attempts: int
    """
    已尝试次数。分级求助的升级判据之一：L0 自修复 → L0.5 Peer-Debug → L1 Helper → L2 War Room → L3 人工。
    ★ 执行体只读，不得据此改变行为。
    """
    evidence: tuple[EvidenceRef, ...]
    """★ I6 证据。执行完成但证据没落盘 = 没做过。"""
    provenance: Provenance
    """溯源图的节点信息，追加不改写。"""


PacketKind = Literal["design", "contract", "impl", "test", "review", "integrate", "spike", "fix", "evolve"]
"""spike = 探索性任务（算法类），产出是结论不是代码；结论固化后另开 impl packet。"""


AcceptanceKind = Literal["test", "metric", "schema", "manual"]
"""metric 用于算法类任务（阈值判定）；manual 需人工审批，应尽量少。"""


class Acceptance(_Base):
    """★ I2 验收可判定。由 QA 在实现之前写，Coder 看不见也改不了 —— 这是三种作弊里「缩小范围」的堵法。"""
    kind: AcceptanceKind
    """metric 用于算法类任务（阈值判定）；manual 需人工审批，应尽量少。"""
    predicate: str
    """机器可判定的判据：可执行命令、指标表达式或 schema 引用。「做得好」不是判据。"""
    threshold: float | None = None
    """kind === 'metric' 时的阈值。"""
    authoredBy: RoleId
    """★ 写这条验收的角色。不得等于 packet 的 role —— 自己给自己定验收即作弊。这条由准入校验器强制。"""


class BudgetGrant(_Base):
    """单个 packet 的预算额度。★ 与 BudgetFile 同样只认美元。"""
    currency: Literal["USD"]
    limitUsd: float
    spentUsd: float
    degradationChain: tuple[str, ...]
    """★ 预算不足时的降级顺序，必须显式声明。"""


class ModelRouting(_Base):
    """模型路由。"""
    model: ModelId
    """模型标识。"""
    effort: Effort
    """推理强度。★ 需要加强时调这个，不要换模型 —— 提示词缓存按模型分桶，同一次尝试内换模型 = 整个前缀冷重写。"""
    batch: bool | None = None
    """延迟不敏感的任务（如 evolve）走 Batch，约五折且无取舍。"""


class Provenance(_Base):
    """溯源图的节点信息，追加不改写。"""
    createdBy: RoleId
    """11 个角色。★ guardian 是唯一 usesModel = false 的 —— 确定性拦截器不碰模型。"""
    createdAt: Timestamp
    """ISO-8601 时间戳。"""
    parent: PacketId | None = None
    """由哪个 packet 派生。"""


# ════════════════════════════════════════════════════════════
#  预算 —— ★ 一律货币，不用 token
# ════════════════════════════════════════════════════════════

class BudgetFile(_Base):
    """
    ★★ 全案预算一律用美元，不用 token。
    不同模型家族的分词器差异可达 ~30%，异构路由下按 token 记预算会静默失真，且失真方向随路由变化，事后查不出来。
    token 数只作可观测指标存在（见 interfaces/model-gateway.ts 的 Usage）。
    """
    schemaVersion: Literal[1]
    currency: Literal["USD"]
    """★ 常量。留这个字段不是为了将来支持多币种，是为了让读代码的人一眼看到「这里是钱不是 token」。"""
    limitUsd: float
    spentUsd: float
    byRole: Mapping[str, float] | None = None
    """按角色分摊。桌面端成本面板读它。"""
    byModel: Mapping[str, float] | None = None
    """按模型分摊。模型路由的调优依据。"""
    degradationChain: tuple[str, ...] | None = None
    """
    ★ 预算不足时的降级顺序，必须显式声明。
    不声明就会退化成随机截断上下文 —— 而随机截断不可复现，replay 随之失效。
    """
    alerts: tuple[BudgetAlert, ...] | None = None
    """已触发的预算告警。桌面端据此显示黄/红。"""


BudgetAlertLevel = Literal["warn", "hard_stop"]


class BudgetAlert(_Base):
    level: BudgetAlertLevel
    at: Timestamp
    """ISO-8601 时间戳。"""
    message: str


# ════════════════════════════════════════════════════════════
#  依赖图 · 所有权图
# ════════════════════════════════════════════════════════════

class GraphFile(_Base):
    """
    依赖图 + 所有权图。
    ★ 四张图性质完全不同，只有所有权图需要并发控制 —— 所以只有它带 version 字段。
    溯源图与知识图不在此文件（见 knowledge.schema.json）。
    """
    schemaVersion: Literal[1]
    dependency: DependencyGraph
    """★ 静态 DAG，规划期确定，不需要并发控制。成环由准入校验器拒绝。"""
    ownership: OwnershipGraph
    """★ 运行时变化，四张图里唯一需要并发控制的。"""


class DependencyEdge(_Base):
    from_: PacketId = Field(alias="from")
    """WorkPacket 的全局唯一 id，创建后不变。"""
    to: PacketId
    """WorkPacket 的全局唯一 id，创建后不变。"""


class DependencyGraph(_Base):
    """★ 静态 DAG，规划期确定，不需要并发控制。成环由准入校验器拒绝。"""
    nodes: tuple[PacketId, ...]
    edges: tuple[DependencyEdge, ...]


class PathLock(_Base):
    pathPrefix: str
    heldBy: PacketId
    """WorkPacket 的全局唯一 id，创建后不变。"""
    acquiredAt: Timestamp
    """ISO-8601 时间戳。"""


class OwnershipGraph(_Base):
    """★ 运行时变化，四张图里唯一需要并发控制的。"""
    locks: tuple[PathLock, ...]
    """
    ★ I1 单写者：任意两条 lock 的 pathPrefix 不得互为前缀关系。
    这条不由 schema 保证（JSON Schema 表达不了），由 control-plane/locks 的前缀树强制，并由 e2e 并发用例验证。
    """
    version: int
    """乐观锁版本号。提交时比对，不一致则拒绝并要求重试。"""


# ════════════════════════════════════════════════════════════
#  溯源图 · 知识图
# ════════════════════════════════════════════════════════════

class KnowledgeFile(_Base):
    """知识图与溯源图的边。★ 这两张图与依赖图/所有权图性质完全不同：溯源图只追加、自动派生；知识图有环。都不需要并发控制。"""
    schemaVersion: Literal[1]
    knowledge: tuple[KnowledgeEdge, ...]
    """知识图的边。★ 有环 —— 三种关系是它区别于前三张图的地方。"""
    provenance: tuple[ProvenanceEdge, ...]
    """溯源图的边。★ 只追加，从 decisions.jsonl 与 artifact 的 derivedFrom 自动派生，不手工登记。"""


class KnowledgeEdge(_Base):
    from_: str = Field(alias="from")
    to: str
    relation: Literal["supports", "refutes", "supersedes"]
    """★ refutes 是证伪门的产物；supersedes 表达退级 —— 记忆不被改回去，而是被新的取代。"""
    confidence: float


class ProvenanceEdge(_Base):
    from_: str = Field(alias="from")
    to: str
    relation: Literal["produced", "derived_from", "accepted_by", "rejected_by"]
    at: Timestamp
    """ISO-8601 时间戳。"""


# ════════════════════════════════════════════════════════════
#  证据 —— I6
# ════════════════════════════════════════════════════════════

class Evidence(_Base):
    """★ I6 证据：状态推进必须附证据引用，声明不算。执行完成但证据没落盘 = 没做过。"""
    ref: EvidenceRef
    """证据引用。★ I6：状态推进必须附证据引用，声明不算。"""
    packetId: PacketId
    """WorkPacket 的全局唯一 id，创建后不变。"""
    role: RoleId
    """11 个角色。★ guardian 是唯一 usesModel = false 的 —— 确定性拦截器不碰模型。"""
    kind: EvidenceKind
    verdict: Verdict
    """
    ★ 必须可复算：拿同样的输入重跑一遍应得到同样的结论。
    不可复算的判定不算证据，只算意见。
    """
    artifacts: tuple[ArtifactRefStr, ...]
    """内容寻址的产出物引用。"""
    prevDigest: Digest
    """★ 审计哈希链：上一条证据的 digest。链首用 sha256: 后跟 64 个 0。断链即篡改。"""
    digest: Digest
    """本条证据的摘要，覆盖除 digest 自身外的全部字段。"""
    at: Timestamp
    """ISO-8601 时间戳。"""
    gate: str | None = None
    """kind === 'gate' 时，是哪道门禁。如 green-line、acceptance、secret-scan。"""
    detail: str | None = None
    """给人看的补充说明。★ 判定不得依赖这个字段 —— 判定看 verdict。"""


EvidenceKind = Literal["test_run", "build", "review", "gate", "tool_log"]


Verdict = Literal["pass", "fail"]
"""
★ 必须可复算：拿同样的输入重跑一遍应得到同样的结论。
不可复算的判定不算证据，只算意见。
"""


# ════════════════════════════════════════════════════════════
#  决策日志
# ════════════════════════════════════════════════════════════

class DecisionRecord(_Base):
    """决策追加日志的一条。★ 只追加不改写 —— 溯源图的边由它派生。"""
    at: Timestamp
    """ISO-8601 时间戳。"""
    actor: RoleId | Literal["operator"]
    """谁做的决定。operator = 人工。"""
    action: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]
    """
    做了什么。如 packet_created、lock_acquired、lock_released、escalation、tool_blocked、approval_required、budget_alert。
    ★ pattern 强制小写下划线 —— 它是给代码 switch 的，不是给人读的。
    """
    packetId: PacketId | None = None
    """WorkPacket 的全局唯一 id，创建后不变。"""
    reasonCode: Annotated[str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_.]*$")]
    """
    ★ 机器可读的理由码，不是自然语言。控制平面据此决策，桌面端据此分类展示。
    ★ pattern 不允许空格与中文 —— 这正是「写成一句话就等于没有理由码」这条的可执行版本。想说人话请写 detail。
    """
    detail: str | None = None
    """给人看的补充说明。★ 任何判定都不得依赖这个字段。"""


# ════════════════════════════════════════════════════════════
#  RoleSpec
# ════════════════════════════════════════════════════════════

class RoleSpec(_Base):
    """一个角色 = 一组写权限 + 一组可见上下文 + 一组可触发的状态转换。提示词只是让它高效地用好这组权限。★ RoleSpec 是 Single Source，派生四处：工具面 / 卷挂载 / 状态转换表 / 所有权注册——任一处手工维护都会漂移，且漂移时通常不报错，只是权限悄悄变宽。"""
    id: RoleId
    """11 个角色。★ guardian 是唯一 usesModel = false 的 —— 确定性拦截器不碰模型。"""
    summary: str | None = None
    """一句话职责。"""
    usesModel: bool
    """
    是否调用模型。
    ★ guardian 必须为 false —— 确定性拦截器不碰模型。
    ⚠️ 这条【不由本 schema 保证】（JSON Schema 表达不了「id=guardian 时 usesModel 必须为 false」这种条件约束），由加载 RoleSpec 的代码强制。不要因为 schema 过了就以为它被检查过。
    """
    writes: tuple[str, ...]
    """可写路径模式。派生卷挂载（读写）与所有权注册。"""
    reads: tuple[str, ...]
    """★ 只读挂载。测试目录与 contracts 对 coder 必须在此而非 writes——这是『改测试』『改契约』两种作弊的堵法，靠挂载权限而非提示词。"""
    invisible: tuple[str, ...] | None = None
    """★ 完全不可见（不出现在上下文里，也无法检索）。约束优先级：不可见 > 无权限 > 被拦截 > 提示词劝阻。reviewer 必须把 coder 的推理链列在这里——存储层强制，不是提示词。"""
    tools: tuple[str, ...]
    """★ 工具面白名单。未列出的工具不出现在工具列表里——是看不见，不是调用时被拒。"""
    transitions: tuple[RoleTransition, ...]
    """此角色可触发的 WorkPacket 状态转换。派生控制平面的状态转换表。"""
    modelPolicy: ModelPolicy | None = None
    """模型隔离用于错误去相关：同一模型既写又审，盲区会在两处同时出现，评审就成了摆设。"""
    skills: tuple[RoleSkill, ...] | None = None
    """此角色可用的 Skill。作用域默认最小。"""
    escalation: EscalationPolicy | None = None
    """分级求助：L0 自修复 → L0.5 Peer-Debug → L1 Helper → L2 War Room → L3 人工。"""
    promptRef: str | None = None
    """packages/roles/prompts/ 下的文件引用。★ 提示词不承载硬约束——判据：这条约束如果 Agent 不配合会怎样？会失效 → 它不该在提示词里。"""


class RoleTransition(_Base):
    from_: PacketState = Field(alias="from")
    """WorkPacket 的状态。★ 定义在这里而不是 workpacket.schema.json —— RoleSpec 的 transitions 也要用它，两处各写一份 enum 就会漂移。合法转换由 RoleSpec 派生的转换表定义，不在此处硬编码。"""
    to: PacketState
    """WorkPacket 的状态。★ 定义在这里而不是 workpacket.schema.json —— RoleSpec 的 transitions 也要用它，两处各写一份 enum 就会漂移。合法转换由 RoleSpec 派生的转换表定义，不在此处硬编码。"""
    requiresGate: str | None = None
    """需要通过的门禁 id，如 green-line、acceptance、secret-scan。"""


class ModelPolicy(_Base):
    """模型隔离用于错误去相关：同一模型既写又审，盲区会在两处同时出现，评审就成了摆设。"""
    defaultModel: ModelId | None = None
    """模型标识。"""
    defaultEffort: Effort | None = None
    """推理强度。★ 需要加强时调这个，不要换模型 —— 提示词缓存按模型分桶，同一次尝试内换模型 = 整个前缀冷重写。"""
    mustDifferFrom: tuple[RoleId, ...] | None = None
    """★ 硬约束，由 ModelGateway.open() 校验，违反则拒绝开会话（不是警告）。coder ≠ reviewer；evolver ≠ verifier。"""
    batchEligible: bool | None = None
    """延迟不敏感（evolver）→ 走 Batch，约五折且无取舍。"""


SkillScope = Literal["global", "role", "once"]
"""三级作用域。默认取最小的 once。"""


SkillState = Literal["draft", "candidate", "active", "deprecated", "retired"]
"""Skill 五态状态机。非法转换由准入校验器拒绝。"""


class RoleSkill(_Base):
    id: str
    scope: SkillScope
    """三级作用域。默认取最小的 once。"""
    state: SkillState | None = None
    """Skill 五态状态机。非法转换由准入校验器拒绝。"""


class EscalationPolicy(_Base):
    """分级求助：L0 自修复 → L0.5 Peer-Debug → L1 Helper → L2 War Room → L3 人工。"""
    maxSelfRepair: int | None = None
    peerDebugEnabled: bool | None = None
    escalateTo: RoleId | None = None
    """升级到哪个角色。典型是 helper。"""
