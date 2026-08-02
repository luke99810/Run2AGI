/**
 * 状态数据类型
 *
 * ⚠️ 本文件由 `python scripts/gen_types.py` 从 packages/contracts/schemas/*.json 生成。
 *    【不要手改】—— 下次生成会覆盖。
 *
 * ★ 与 packages/contracts/python/codentum_contracts/state.py 同源。
 *   两侧都是生成物，谁都不手写 —— 这是跨语言边界唯一有效的防漂移手段。
 *
 * 真源：packages/contracts/schemas/*.json
 * 生成器：scripts/gen_types.py
 */

// ══════════════════════════════════════════════════════════
//  标识
// ══════════════════════════════════════════════════════════

/** WorkPacket 的全局唯一 id，创建后不变。 */
export type PacketId = string & { readonly __brand: 'PacketId' };

/** 证据引用。★ I6：状态推进必须附证据引用，声明不算。 */
export type EvidenceRef = string & { readonly __brand: 'EvidenceRef' };

/** 内容寻址的产出物引用，格式 `<algo>:<hex>`。★ 内容寻址意味着 ref 变了就一定是内容变了，不可能同名不同物 —— 溯源图的可信度建立在这上面。 */
export type ArtifactRefStr = string;

/**
 * 摘要，格式 `sha256:<64位小写十六进制>`。
 * ★ 审计哈希链的链首用 sha256: 后跟 64 个 0 —— 哨兵值也必须是合法摘要格式，否则解析链首要走特例分支，而特例分支是最少被测到的分支。
 */
export type Digest = string;

/**
 * WorkPacket 的状态。★ 定义在这里而不是 workpacket.schema.json —— RoleSpec 的 transitions 也要用它，两处各写一份 enum 就会漂移。合法转换由 RoleSpec 派生的转换表定义，不在此处硬编码。
 */
export type PacketState = "pending" | "ready" | "running" | "blocked" | "review" | "accepted" | "rejected" | "abandoned";

/** 11 个角色。★ guardian 是唯一 usesModel = false 的 —— 确定性拦截器不碰模型。 */
export type RoleId = "intake" | "architect" | "planner" | "qa" | "coder" | "helper" | "reviewer" | "integrator" | "manager" | "evolver" | "guardian";

/** 模型标识。 */
export type ModelId = string;

/** 推理强度。★ 需要加强时调这个，不要换模型 —— 提示词缓存按模型分桶，同一次尝试内换模型 = 整个前缀冷重写。 */
export type Effort = "low" | "medium" | "high" | "xhigh" | "max";

/** ISO-8601 时间戳。 */
export type Timestamp = string;

// ══════════════════════════════════════════════════════════
//  WorkPacket
// ══════════════════════════════════════════════════════════

/** 调度的最小单位。一个 WorkPacket = 一段可独占的写权限 + 一条可判定的验收 + 一份预算。对应 K8s 的 Pod spec。 */
export interface WorkPacket {
  /** WorkPacket 的全局唯一 id，创建后不变。 */
  readonly id: PacketId;
  /** spike = 探索性任务（算法类），产出是结论不是代码；结论固化后另开 impl packet。 */
  readonly kind: PacketKind;
  /**
   * WorkPacket 的状态。★ 定义在这里而不是 workpacket.schema.json —— RoleSpec 的 transitions 也要用它，两处各写一份 enum 就会漂移。合法转换由 RoleSpec 派生的转换表定义，不在此处硬编码。
   */
  readonly state: PacketState;
  /** 11 个角色。★ guardian 是唯一 usesModel = false 的 —— 确定性拦截器不碰模型。 */
  readonly role: RoleId;
  /**
   * ★ I1 单写者：运行期间独占写权限。与任何其他 running packet 的 ownsPaths 不得相交 —— 由 control-plane/locks 强制，不靠约定。生成物不进 ownsPaths。
   * 注意：pending 的 packet 可以声明与在跑的锁重叠的路径（如集成 packet 声明 src/），锁只对 running 生效。
   */
  readonly ownsPaths: readonly string[];
  /**
   * 只读挂载。★ 约束靠挂载权限物理生效，不靠提示词劝阻。
   * 对 coder 而言 tests/ 与 packages/contracts/ 必须在这里而不是 ownsPaths —— 这是「改测试」「改契约」两种作弊的堵法。
   */
  readonly readsPaths: readonly string[];
  /** 依赖图的边。必须是 DAG —— 成环由准入校验器拒绝。 */
  readonly deps: readonly PacketId[];
  /** ★ I2 验收可判定。由 QA 在实现之前写，Coder 看不见也改不了 —— 这是三种作弊里「缩小范围」的堵法。 */
  readonly acceptance: Acceptance;
  /** 本 packet 的预算额度。 */
  readonly budget: BudgetGrant;
  /** 留空则用角色默认（RoleSpec.modelPolicy）。 */
  readonly routing?: ModelRouting;
  /**
   * 已尝试次数。分级求助的升级判据之一：L0 自修复 → L0.5 Peer-Debug → L1 Helper → L2 War Room → L3 人工。
   * ★ 执行体只读，不得据此改变行为。
   */
  readonly attempts: number;
  /** ★ I6 证据。执行完成但证据没落盘 = 没做过。 */
  readonly evidence: readonly EvidenceRef[];
  /** 溯源图的节点信息，追加不改写。 */
  readonly provenance: Provenance;
}

/** spike = 探索性任务（算法类），产出是结论不是代码；结论固化后另开 impl packet。 */
export type PacketKind = "design" | "contract" | "impl" | "test" | "review" | "integrate" | "spike" | "fix" | "evolve";

/** metric 用于算法类任务（阈值判定）；manual 需人工审批，应尽量少。 */
export type AcceptanceKind = "test" | "metric" | "schema" | "manual";

/** ★ I2 验收可判定。由 QA 在实现之前写，Coder 看不见也改不了 —— 这是三种作弊里「缩小范围」的堵法。 */
export interface Acceptance {
  /** metric 用于算法类任务（阈值判定）；manual 需人工审批，应尽量少。 */
  readonly kind: AcceptanceKind;
  /** 机器可判定的判据：可执行命令、指标表达式或 schema 引用。「做得好」不是判据。 */
  readonly predicate: string;
  /** kind === 'metric' 时的阈值。 */
  readonly threshold?: number;
  /** ★ 写这条验收的角色。不得等于 packet 的 role —— 自己给自己定验收即作弊。这条由准入校验器强制。 */
  readonly authoredBy: RoleId;
}

/** 单个 packet 的预算额度。★ 与 BudgetFile 同样只认美元。 */
export interface BudgetGrant {
  readonly currency: "USD";
  readonly limitUsd: number;
  readonly spentUsd: number;
  /** ★ 预算不足时的降级顺序，必须显式声明。 */
  readonly degradationChain: readonly string[];
}

/** 模型路由。 */
export interface ModelRouting {
  /** 模型标识。 */
  readonly model: ModelId;
  /** 推理强度。★ 需要加强时调这个，不要换模型 —— 提示词缓存按模型分桶，同一次尝试内换模型 = 整个前缀冷重写。 */
  readonly effort: Effort;
  /** 延迟不敏感的任务（如 evolve）走 Batch，约五折且无取舍。 */
  readonly batch?: boolean;
}

/** 溯源图的节点信息，追加不改写。 */
export interface Provenance {
  /** 11 个角色。★ guardian 是唯一 usesModel = false 的 —— 确定性拦截器不碰模型。 */
  readonly createdBy: RoleId;
  /** ISO-8601 时间戳。 */
  readonly createdAt: Timestamp;
  /** 由哪个 packet 派生。 */
  readonly parent?: PacketId;
}

// ══════════════════════════════════════════════════════════
//  预算 —— ★ 一律货币，不用 token
// ══════════════════════════════════════════════════════════

/**
 * ★★ 全案预算一律用美元，不用 token。
 * 不同模型家族的分词器差异可达 ~30%，异构路由下按 token 记预算会静默失真，且失真方向随路由变化，事后查不出来。
 * token 数只作可观测指标存在（见 interfaces/model-gateway.ts 的 Usage）。
 */
export interface BudgetFile {
  readonly schemaVersion: 1;
  /** ★ 常量。留这个字段不是为了将来支持多币种，是为了让读代码的人一眼看到「这里是钱不是 token」。 */
  readonly currency: "USD";
  readonly limitUsd: number;
  readonly spentUsd: number;
  /** 按角色分摊。桌面端成本面板读它。 */
  readonly byRole?: Readonly<Record<string, number>>;
  /** 按模型分摊。模型路由的调优依据。 */
  readonly byModel?: Readonly<Record<string, number>>;
  /**
   * ★ 预算不足时的降级顺序，必须显式声明。
   * 不声明就会退化成随机截断上下文 —— 而随机截断不可复现，replay 随之失效。
   */
  readonly degradationChain?: readonly string[];
  /** 已触发的预算告警。桌面端据此显示黄/红。 */
  readonly alerts?: readonly BudgetAlert[];
}

export type BudgetAlertLevel = "warn" | "hard_stop";

export interface BudgetAlert {
  readonly level: BudgetAlertLevel;
  /** ISO-8601 时间戳。 */
  readonly at: Timestamp;
  readonly message: string;
}

// ══════════════════════════════════════════════════════════
//  依赖图 · 所有权图
// ══════════════════════════════════════════════════════════

/**
 * 依赖图 + 所有权图。
 * ★ 四张图性质完全不同，只有所有权图需要并发控制 —— 所以只有它带 version 字段。
 * 溯源图与知识图不在此文件（见 knowledge.schema.json）。
 */
export interface GraphFile {
  readonly schemaVersion: 1;
  /** ★ 静态 DAG，规划期确定，不需要并发控制。成环由准入校验器拒绝。 */
  readonly dependency: DependencyGraph;
  /** ★ 运行时变化，四张图里唯一需要并发控制的。 */
  readonly ownership: OwnershipGraph;
}

export interface DependencyEdge {
  /** WorkPacket 的全局唯一 id，创建后不变。 */
  readonly from: PacketId;
  /** WorkPacket 的全局唯一 id，创建后不变。 */
  readonly to: PacketId;
}

/** ★ 静态 DAG，规划期确定，不需要并发控制。成环由准入校验器拒绝。 */
export interface DependencyGraph {
  readonly nodes: readonly PacketId[];
  readonly edges: readonly DependencyEdge[];
}

export interface PathLock {
  readonly pathPrefix: string;
  /** WorkPacket 的全局唯一 id，创建后不变。 */
  readonly heldBy: PacketId;
  /** ISO-8601 时间戳。 */
  readonly acquiredAt: Timestamp;
}

/** ★ 运行时变化，四张图里唯一需要并发控制的。 */
export interface OwnershipGraph {
  /**
   * ★ I1 单写者：任意两条 lock 的 pathPrefix 不得互为前缀关系。
   * 这条不由 schema 保证（JSON Schema 表达不了），由 control-plane/locks 的前缀树强制，并由 e2e 并发用例验证。
   */
  readonly locks: readonly PathLock[];
  /** 乐观锁版本号。提交时比对，不一致则拒绝并要求重试。 */
  readonly version: number;
}

// ══════════════════════════════════════════════════════════
//  溯源图 · 知识图
// ══════════════════════════════════════════════════════════

/** 知识图与溯源图的边。★ 这两张图与依赖图/所有权图性质完全不同：溯源图只追加、自动派生；知识图有环。都不需要并发控制。 */
export interface KnowledgeFile {
  readonly schemaVersion: 1;
  /** 知识图的边。★ 有环 —— 三种关系是它区别于前三张图的地方。 */
  readonly knowledge: readonly KnowledgeEdge[];
  /** 溯源图的边。★ 只追加，从 decisions.jsonl 与 artifact 的 derivedFrom 自动派生，不手工登记。 */
  readonly provenance: readonly ProvenanceEdge[];
}

export interface KnowledgeEdge {
  readonly from: string;
  readonly to: string;
  /** ★ refutes 是证伪门的产物；supersedes 表达退级 —— 记忆不被改回去，而是被新的取代。 */
  readonly relation: "supports" | "refutes" | "supersedes";
  readonly confidence: number;
}

export interface ProvenanceEdge {
  readonly from: string;
  readonly to: string;
  readonly relation: "produced" | "derived_from" | "accepted_by" | "rejected_by";
  /** ISO-8601 时间戳。 */
  readonly at: Timestamp;
}

// ══════════════════════════════════════════════════════════
//  证据 —— I6
// ══════════════════════════════════════════════════════════

/** ★ I6 证据：状态推进必须附证据引用，声明不算。执行完成但证据没落盘 = 没做过。 */
export interface Evidence {
  /** 证据引用。★ I6：状态推进必须附证据引用，声明不算。 */
  readonly ref: EvidenceRef;
  /** WorkPacket 的全局唯一 id，创建后不变。 */
  readonly packetId: PacketId;
  /** 11 个角色。★ guardian 是唯一 usesModel = false 的 —— 确定性拦截器不碰模型。 */
  readonly role: RoleId;
  readonly kind: EvidenceKind;
  /**
   * ★ 必须可复算：拿同样的输入重跑一遍应得到同样的结论。
   * 不可复算的判定不算证据，只算意见。
   */
  readonly verdict: Verdict;
  /** 内容寻址的产出物引用。 */
  readonly artifacts: readonly ArtifactRefStr[];
  /** ★ 审计哈希链：上一条证据的 digest。链首用 sha256: 后跟 64 个 0。断链即篡改。 */
  readonly prevDigest: Digest;
  /** 本条证据的摘要，覆盖除 digest 自身外的全部字段。 */
  readonly digest: Digest;
  /** ISO-8601 时间戳。 */
  readonly at: Timestamp;
  /** kind === 'gate' 时，是哪道门禁。如 green-line、acceptance、secret-scan。 */
  readonly gate?: string;
  /** 给人看的补充说明。★ 判定不得依赖这个字段 —— 判定看 verdict。 */
  readonly detail?: string;
}

export type EvidenceKind = "test_run" | "build" | "review" | "gate" | "tool_log";

/**
 * ★ 必须可复算：拿同样的输入重跑一遍应得到同样的结论。
 * 不可复算的判定不算证据，只算意见。
 */
export type Verdict = "pass" | "fail";

// ══════════════════════════════════════════════════════════
//  决策日志
// ══════════════════════════════════════════════════════════

/** 决策追加日志的一条。★ 只追加不改写 —— 溯源图的边由它派生。 */
export interface DecisionRecord {
  /** ISO-8601 时间戳。 */
  readonly at: Timestamp;
  /** 谁做的决定。operator = 人工。 */
  readonly actor: RoleId | "operator";
  /**
   * 做了什么。如 packet_created、lock_acquired、lock_released、escalation、tool_blocked、approval_required、budget_alert。
   * ★ pattern 强制小写下划线 —— 它是给代码 switch 的，不是给人读的。
   */
  readonly action: string;
  /** WorkPacket 的全局唯一 id，创建后不变。 */
  readonly packetId?: PacketId;
  /**
   * ★ 机器可读的理由码，不是自然语言。控制平面据此决策，桌面端据此分类展示。
   * ★ pattern 不允许空格与中文 —— 这正是「写成一句话就等于没有理由码」这条的可执行版本。想说人话请写 detail。
   */
  readonly reasonCode: string;
  /** 给人看的补充说明。★ 任何判定都不得依赖这个字段。 */
  readonly detail?: string;
}

// ══════════════════════════════════════════════════════════
//  RoleSpec
// ══════════════════════════════════════════════════════════

/**
 * 一个角色 = 一组写权限 + 一组可见上下文 + 一组可触发的状态转换。提示词只是让它高效地用好这组权限。★ RoleSpec 是 Single Source，派生四处：工具面 / 卷挂载 / 状态转换表 / 所有权注册——任一处手工维护都会漂移，且漂移时通常不报错，只是权限悄悄变宽。
 */
export interface RoleSpec {
  /** 11 个角色。★ guardian 是唯一 usesModel = false 的 —— 确定性拦截器不碰模型。 */
  readonly id: RoleId;
  /** 一句话职责。 */
  readonly summary?: string;
  /**
   * 是否调用模型。
   * ★ guardian 必须为 false —— 确定性拦截器不碰模型。
   * ⚠️ 这条【不由本 schema 保证】（JSON Schema 表达不了「id=guardian 时 usesModel 必须为 false」这种条件约束），由加载 RoleSpec 的代码强制。不要因为 schema 过了就以为它被检查过。
   */
  readonly usesModel: boolean;
  /** 可写路径模式。派生卷挂载（读写）与所有权注册。 */
  readonly writes: readonly string[];
  /** ★ 只读挂载。测试目录与 contracts 对 coder 必须在此而非 writes——这是『改测试』『改契约』两种作弊的堵法，靠挂载权限而非提示词。 */
  readonly reads: readonly string[];
  /**
   * ★ 完全不可见（不出现在上下文里，也无法检索）。约束优先级：不可见 > 无权限 > 被拦截 > 提示词劝阻。reviewer 必须把 coder 的推理链列在这里——存储层强制，不是提示词。
   */
  readonly invisible?: readonly string[];
  /** ★ 工具面白名单。未列出的工具不出现在工具列表里——是看不见，不是调用时被拒。 */
  readonly tools: readonly string[];
  /** 此角色可触发的 WorkPacket 状态转换。派生控制平面的状态转换表。 */
  readonly transitions: readonly RoleTransition[];
  /** 模型隔离用于错误去相关：同一模型既写又审，盲区会在两处同时出现，评审就成了摆设。 */
  readonly modelPolicy?: ModelPolicy;
  /** 此角色可用的 Skill。作用域默认最小。 */
  readonly skills?: readonly RoleSkill[];
  /** 分级求助：L0 自修复 → L0.5 Peer-Debug → L1 Helper → L2 War Room → L3 人工。 */
  readonly escalation?: EscalationPolicy;
  /** packages/roles/prompts/ 下的文件引用。★ 提示词不承载硬约束——判据：这条约束如果 Agent 不配合会怎样？会失效 → 它不该在提示词里。 */
  readonly promptRef?: string;
}

export interface RoleTransition {
  /**
   * WorkPacket 的状态。★ 定义在这里而不是 workpacket.schema.json —— RoleSpec 的 transitions 也要用它，两处各写一份 enum 就会漂移。合法转换由 RoleSpec 派生的转换表定义，不在此处硬编码。
   */
  readonly from: PacketState;
  /**
   * WorkPacket 的状态。★ 定义在这里而不是 workpacket.schema.json —— RoleSpec 的 transitions 也要用它，两处各写一份 enum 就会漂移。合法转换由 RoleSpec 派生的转换表定义，不在此处硬编码。
   */
  readonly to: PacketState;
  /** 需要通过的门禁 id，如 green-line、acceptance、secret-scan。 */
  readonly requiresGate?: string;
}

/** 模型隔离用于错误去相关：同一模型既写又审，盲区会在两处同时出现，评审就成了摆设。 */
export interface ModelPolicy {
  /** 模型标识。 */
  readonly defaultModel?: ModelId;
  /** 推理强度。★ 需要加强时调这个，不要换模型 —— 提示词缓存按模型分桶，同一次尝试内换模型 = 整个前缀冷重写。 */
  readonly defaultEffort?: Effort;
  /** ★ 硬约束，由 ModelGateway.open() 校验，违反则拒绝开会话（不是警告）。coder ≠ reviewer；evolver ≠ verifier。 */
  readonly mustDifferFrom?: readonly RoleId[];
  /** 延迟不敏感（evolver）→ 走 Batch，约五折且无取舍。 */
  readonly batchEligible?: boolean;
}

/** 三级作用域。默认取最小的 once。 */
export type SkillScope = "global" | "role" | "once";

/** Skill 五态状态机。非法转换由准入校验器拒绝。 */
export type SkillState = "draft" | "candidate" | "active" | "deprecated" | "retired";

export interface RoleSkill {
  readonly id: string;
  /** 三级作用域。默认取最小的 once。 */
  readonly scope: SkillScope;
  /** Skill 五态状态机。非法转换由准入校验器拒绝。 */
  readonly state?: SkillState;
}

/** 分级求助：L0 自修复 → L0.5 Peer-Debug → L1 Helper → L2 War Room → L3 人工。 */
export interface EscalationPolicy {
  readonly maxSelfRepair?: number;
  readonly peerDebugEnabled?: boolean;
  /** 升级到哪个角色。典型是 helper。 */
  readonly escalateTo?: RoleId;
}
