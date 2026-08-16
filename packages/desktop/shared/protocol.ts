import type {
  BudgetFile,
  DecisionRecord,
  Evidence,
  GraphFile,
  KnowledgeFile,
  PacketState,
  RoleId,
  RoleSpec,
  WorkPacket
} from '@codentum/contracts'

export type SnapshotSourceKind = 'fixture' | 'project'
export type ProjectSelectionKind = 'file' | 'folder'
export type ManagedResourceKind = 'plugin' | 'knowledge' | 'skill'
export type ManagedResourceSourceKind = 'file' | 'folder' | 'git_url'
export type ManagedResourceScope = 'global' | 'role' | 'project'
export type ManagedResourceRuntimeStatus = 'registered' | 'pending_runtime' | 'missing_source'

export interface ManagedResource {
  readonly id: string
  readonly kind: ManagedResourceKind
  readonly name: string
  readonly description: string
  readonly sourceKind: ManagedResourceSourceKind
  readonly sourceLabel: string
  readonly scope: ManagedResourceScope
  readonly roleId?: string
  readonly enabled: boolean
  readonly runtimeStatus: ManagedResourceRuntimeStatus
  readonly addedAt: string
  readonly updatedAt: string
}

export interface ManagedResourcePatch {
  readonly enabled?: boolean
  readonly scope?: ManagedResourceScope
  readonly roleId?: string
}

export type ConnectorProvider = 'custom'

export interface ConnectorConfiguration {
  readonly id: string
  readonly provider: ConnectorProvider
  readonly name: string
  readonly accountLabel: string
  readonly enabled: boolean
  readonly credentialConfigured: boolean
  readonly updatedAt: string
}

export interface ConnectorConfigurationInput {
  readonly id?: string
  readonly provider: ConnectorProvider
  readonly name: string
  readonly accountLabel: string
  readonly enabled: boolean
  readonly credential?: string
  readonly clearCredential?: boolean
}

/**
 * 模型接入的三个作用域。
 *
 * ★ `global` 与 `orchestrator` 不是角色，是**作用域**。它们借用 roleId 这个
 *   字段位存放，因为存储层本来就按 roleId 索引；用两个保留值而不是新开一张表，
 *   是为了让「三级配置」在存储、IPC、界面三处都是同一个形状。
 *
 * ★ `orchestrator`（主 Agent）在引擎侧对应 planner 角色。名字不同是因为
 *   对使用者而言它是「那个统筹的」，而代码里它是 11 个角色之一。
 *   两边的映射写在引擎的 `model_config.ORCHESTRATOR_ROLE`，只此一处。
 */
export const GLOBAL_SCOPE = '__global__'
export const ORCHESTRATOR_SCOPE = '__orchestrator__'

/**
 * 一层模型接入配置。每个字段都可缺省，缺省即穿透到下一层。
 *
 * ★ 字段级穿透（而不是整块覆盖）是刻意的：只想给某个 Agent 提高 effort 的人，
 *   不该被迫把 model / baseUrl 全抄一遍 —— 抄一遍就意味着全局改了之后
 *   这个 Agent 不会跟着改，而**那种漂移不会有任何东西报错**。
 */
export interface ModelEndpoint {
  readonly model?: string
  readonly effort?: ModelEffort
  readonly baseUrl?: string
}

export type ModelEffort = 'low' | 'medium' | 'high' | 'xhigh' | 'max'

export const MODEL_EFFORTS: readonly ModelEffort[] = ['low', 'medium', 'high', 'xhigh', 'max']

export interface AgentConfiguration {
  readonly roleId: string
  readonly name?: string
  readonly custom?: boolean
  readonly systemPrompt: string
  readonly systemDocumentName?: string
  readonly apiKeyConfigured: boolean
  /** 这一层显式配置的模型接入参数（未配的字段不出现）。 */
  readonly endpoint?: ModelEndpoint
  readonly updatedAt: string
}

/**
 * 提交用的接入参数。**与存储用的 `ModelEndpoint` 刻意不是同一个类型。**
 *
 * ★ 区别只在 effort 允许空串：空串 = 取消这一层的配置（回落到下一层），
 *   字段不传 = 不改动。这两者必须可区分 —— 界面上把下拉框选回
 *   「跟随上一层」是**取消**，不是「无操作」。
 *
 * ★ 这里差点出过事：为了让 `exactOptionalPropertyTypes` 通过，
 *   一度把空 effort 写成「省略该字段」—— 类型检查绿了，
 *   而清除功能变成了静默无操作。**为了让类型通过而改变语义，
 *   是最容易漏掉的一类缺陷。**
 */
export interface ModelEndpointPatch {
  readonly model?: string
  readonly effort?: ModelEffort | ''
  readonly baseUrl?: string
}

export interface AgentConfigurationPatch {
  readonly name?: string
  readonly systemPrompt?: string
  readonly apiKey?: string
  readonly clearApiKey?: boolean
  /** 见 `ModelEndpointPatch`：空串 = 取消这一层，不传 = 不改动。 */
  readonly endpoint?: ModelEndpointPatch
}

/**
 * 某个 Agent 当前**实际生效**的配置与运行指标，来自引擎写的
 * `.codentum/agents.json`（只读投影，不是配置）。
 *
 * ★ `source` 说明每个值来自哪一层。没有它，「我明明配了却没生效」
 *   只能靠猜 —— 而那有三种原因（没保存 / 被 RoleSpec 覆盖 / 引擎没读到），
 *   现象完全一样，修法完全不同。
 */
export interface AgentRuntimeProfile {
  readonly role: string
  readonly config?: {
    readonly model?: string
    readonly effort?: string
    readonly baseUrl?: string | null
    readonly apiKeyEnv?: string | null
    readonly source?: Readonly<Record<string, string>>
  }
  readonly packets: { readonly total: number; readonly byState: Readonly<Record<string, number>> }
  readonly attempts: number
  readonly spentCny: number
  readonly lastActivityAt?: string | null
}

export interface McpConfiguration {
  readonly id: string
  readonly name: string
  readonly transport: 'stdio' | 'http' | 'sse'
  readonly endpoint: string
  readonly enabled: boolean
  readonly credentialConfigured: boolean
  readonly updatedAt: string
}

export interface McpConfigurationInput {
  readonly id?: string
  readonly name: string
  readonly transport: 'stdio' | 'http' | 'sse'
  readonly endpoint: string
  readonly enabled: boolean
  readonly credential?: string
  readonly clearCredential?: boolean
}

/**
 * C sends this stable request shape through A's submit_requirement payload.
 * A archives it today; B can consume it when resolving RoleSpec/ToolSurface.
 */
export interface ResourceSelection {
  readonly id: string
  readonly kind: ManagedResourceKind
  readonly scope: ManagedResourceScope
  readonly roleId?: string
  readonly sourceKind: ManagedResourceSourceKind
  readonly localPath?: string
  readonly gitUrl?: string
}

export interface SnapshotSourceDescriptor {
  readonly id: string
  readonly kind: SnapshotSourceKind
  readonly label: string
  readonly rootPath?: string
}

export interface DraftAttachment {
  readonly id: string
  readonly name: string
  readonly kind: 'file' | 'folder'
  readonly fileCount: number
  readonly sizeBytes: number
  readonly sha256: string
}

export interface RequirementDraftSnapshot {
  readonly text: string
  readonly attachments: readonly DraftAttachment[]
}

export const MAX_DRAFT_ATTACHMENTS = 20
export const MAX_REQUIREMENT_DRAFT_CHARS = 200_000

export type WorkerState = 'starting' | 'running' | 'waiting' | 'completed' | 'failed' | 'aborted' | 'unknown'

export interface WorkerEventProjection {
  readonly seq: number
  readonly kind: string
  readonly at: string
  readonly payload: Readonly<Record<string, unknown>>
}

export interface WorkerProjection {
  readonly workerId: string
  readonly packetId: string
  readonly role: RoleId | string
  readonly attempt: number
  readonly state: WorkerState
  readonly startedAt?: string
  readonly finishedAt?: string
  readonly currentModule?: string
  readonly spentCny?: number
  readonly workspace?: string
  readonly events: readonly WorkerEventProjection[]
}

export type McpServiceStatus = 'connected' | 'connecting' | 'disconnected' | 'error'

export interface McpServiceProjection {
  readonly id: string
  readonly name: string
  readonly transport: 'stdio' | 'http' | 'sse'
  readonly status: McpServiceStatus
  readonly authentication: 'not_required' | 'configured' | 'missing' | 'unknown'
  readonly tools: readonly string[]
  readonly category?: string
  readonly purpose?: string
  readonly command?: string
  readonly args?: readonly string[]
  readonly enabled?: boolean
  readonly requiresEnv?: readonly string[]
  readonly credentialHowTo?: string
  readonly docs?: string
  readonly configSource?: string
  readonly error?: string
}

export interface SkillProjection {
  readonly id: string
  readonly version: string
  readonly scope: 'global' | 'role' | 'once'
  readonly appliesTo: readonly string[]
  readonly description: string
  readonly inputs: Readonly<Record<string, string>>
  readonly outputs: Readonly<Record<string, string>>
  readonly preconditions: readonly string[]
  readonly failure: {
    readonly timeoutSeconds: number
    readonly onError: string
    readonly silentDegrade: boolean
  }
  readonly permissions: {
    readonly riskLevel: string
    readonly tools: readonly string[]
    readonly readPaths: readonly string[]
    readonly writePaths: readonly string[]
    readonly networkAccess: readonly string[]
  }
  readonly requiresMcp: readonly string[]
  readonly requiresSkills: readonly string[]
  readonly conflicts: readonly string[]
  readonly reuse: {
    readonly crossRole: boolean
    readonly crossProject: boolean
  }
  /** B projects the executable Skill instructions beside manifest.json. */
  readonly instructionMarkdown: string
}

export interface RequirementProjection {
  readonly packetId: string
  readonly text: string
  readonly submittedAt: string
  readonly commandId: string
  readonly taskId?: string
}

export interface ArtifactPackageResult {
  readonly fileName: string
  readonly sha256: string
  readonly fileCount: number
  readonly sourceBytes: number
  readonly archiveBytes: number
  readonly packetId?: string
  readonly verified: boolean
  readonly createdAt: string
  readonly log: readonly string[]
}

/**
 * Optional A-side projection. C reads it when present but never derives limits
 * or queue order from the visible packet list.
 */
export interface SchedulingProjection {
  readonly schemaVersion: 1
  readonly revision?: number
  readonly updatedAt?: string
  readonly wipLimits: Readonly<Partial<Record<PacketState, number>>>
  readonly readyQueue?: readonly string[]
  readonly criticalPath?: readonly string[]
}

export type FlowActivityKind = 'waiting' | 'value'

export interface PacketFlowSegmentProjection {
  readonly state: PacketState
  readonly kind: FlowActivityKind
  readonly durationMs: number
  readonly startedAt?: string
  readonly endedAt?: string
  readonly reason?: string
}

export interface PacketFlowProjection {
  readonly packetId: string
  readonly totalCycleMs: number
  readonly efficiency?: number
  readonly segments: readonly PacketFlowSegmentProjection[]
}

export interface FlowStageProjection {
  readonly state: PacketState
  readonly packetCount: number
  readonly waitP50Ms?: number
  readonly waitP80Ms?: number
}

export interface BottleneckProjection {
  readonly state: PacketState
  readonly waitP80Ms: number
  readonly affectedPackets: number
  readonly recommendation?: string
}

export interface AndonProjection {
  readonly id: string
  readonly packetId: string
  readonly severity: 'warning' | 'critical'
  readonly reason: string
  readonly consecutiveFailures?: number
  readonly evidenceRefs?: readonly string[]
  readonly at: string
}

/** Optional deterministic flow metrics projected by A/B into flow.json. */
export interface FlowProjection {
  readonly schemaVersion: 1
  readonly calculatedAt?: string
  readonly efficiency?: number
  readonly stages: readonly FlowStageProjection[]
  readonly packets: readonly PacketFlowProjection[]
  readonly bottleneck?: BottleneckProjection
  readonly andons: readonly AndonProjection[]
}

export interface SkillProjectionItem {
  readonly id: string
  readonly name: string
  readonly description?: string
  readonly origin: 'local' | 'cloud' | string
  readonly sourceId?: string
  readonly sourcePath?: string
  readonly sourceCatalog?: string
  readonly role?: string
  readonly matchScore?: number
}

export interface SkillCloudSearchProjection {
  readonly enabled: boolean
  readonly catalog: string
  readonly query: string
  readonly matchedCount: number
  readonly selected: readonly {
    readonly id: string
    readonly name: string
    readonly sourceId: string
    readonly matchScore?: number
  }[]
  readonly degraded: boolean
  readonly degradationReasons: readonly string[]
}

export interface SkillRuntimeProjection {
  readonly schemaVersion: 1
  readonly updatedAt: string
  readonly packetId: string
  readonly role: string
  readonly sharedDir: string
  readonly projectedCount: number
  readonly projected: readonly SkillProjectionItem[]
  readonly cloudSearch: SkillCloudSearchProjection
  readonly degraded: boolean
  readonly degradationReasons: readonly string[]
}

export interface StateSnapshot {
  readonly source: SnapshotSourceDescriptor
  readonly revision: string
  readonly readAt: string
  readonly graph: GraphFile | null
  readonly packets: readonly WorkPacket[]
  readonly budget: BudgetFile | null
  readonly decisions: readonly DecisionRecord[]
  readonly evidence: readonly Evidence[]
  readonly knowledge: KnowledgeFile | null
  readonly roles: readonly RoleSpec[]
  readonly skills: readonly SkillProjection[]
  readonly requirements: readonly RequirementProjection[]
  readonly skillProjection: SkillRuntimeProjection | null
  readonly mcpServices: readonly McpServiceProjection[]
  readonly workers: readonly WorkerProjection[]
  readonly scheduling: SchedulingProjection | null
  readonly flow: FlowProjection | null
  /** 各子 Agent 的生效配置与运行指标，来自引擎写的 `.codentum/agents.json`。 */
  readonly agents: readonly AgentRuntimeProfile[]
  readonly warnings: readonly string[]
}

export type EngineCapability =
  | 'requirements'
  | 'planConfirmation'
  | 'pauseAtSafePoint'
  | 'resume'
  | 'stop'
  | 'keepMemory'
  | 'forkFromCheckpoint'
  | 'appendPrompt'
  | 'insertModule'

export type CapabilityMap = Readonly<Record<EngineCapability, boolean>>

export interface EngineHandshake {
  readonly connected: boolean
  readonly protocolVersion: number
  readonly engineVersion: string
  readonly stateRevision: number
  readonly runId?: string
  readonly projectRoot?: string
  readonly capabilities: CapabilityMap
  readonly unavailableReason?: string
}

export type OperatorAction =
  | 'submit_requirement'
  | 'confirm_plan'
  | 'pause_at_safe_point'
  | 'resume'
  | 'stop'
  | 'stop_keep_memory'
  | 'fork_from_checkpoint'
  | 'append_prompt'
  | 'insert_module'

export interface OperatorCommand {
  readonly commandId: string
  readonly runId: string
  readonly expectedRevision: number
  readonly target: {
    readonly agentId: string
    readonly packetId?: string
    readonly moduleId?: string
  }
  readonly action: OperatorAction
  readonly payload: Readonly<Record<string, unknown>>
  readonly requestedAt: string
}

export type CommandReceiptStatus = 'accepted' | 'waiting_safe_point' | 'applied' | 'rejected'

export interface CommandReceipt {
  readonly commandId: string
  readonly status: CommandReceiptStatus
  readonly stateRevision: number
  readonly receivedAt: string
  readonly reason?: string
}

export interface DesktopBridge {
  listSources(): Promise<readonly SnapshotSourceDescriptor[]>
  readSnapshot(sourceId: string): Promise<StateSnapshot>
  selectProject(kind: ProjectSelectionKind): Promise<SnapshotSourceDescriptor | null>
  selectDraftFiles(scopeId: string): Promise<RequirementDraftSnapshot>
  selectDraftFolders(scopeId: string): Promise<RequirementDraftSnapshot>
  loadRequirementDraft(scopeId: string): Promise<RequirementDraftSnapshot>
  saveRequirementDraft(scopeId: string, draft: RequirementDraftSnapshot): Promise<void>
  moveRequirementDraft(sourceScopeId: string, targetScopeId: string): Promise<RequirementDraftSnapshot>
  discardDraftAttachment(scopeId: string, attachmentId: string): Promise<RequirementDraftSnapshot>
  exportTaskRecord(suggestedName: string, markdown: string): Promise<boolean>
  packageProjectArtifact(sourceId: string, suggestedName: string, packetId?: string): Promise<ArtifactPackageResult | null>
  listManagedResources(kind?: ManagedResourceKind): Promise<readonly ManagedResource[]>
  selectManagedResources(kind: ManagedResourceKind, sourceKind: 'file' | 'folder'): Promise<readonly ManagedResource[]>
  addManagedResourceUrl(kind: ManagedResourceKind, url: string): Promise<ManagedResource>
  updateManagedResource(id: string, patch: ManagedResourcePatch): Promise<ManagedResource>
  removeManagedResource(id: string): Promise<boolean>
  listConnectors(): Promise<readonly ConnectorConfiguration[]>
  saveConnector(input: ConnectorConfigurationInput): Promise<ConnectorConfiguration>
  removeConnector(id: string): Promise<boolean>
  listAgentConfigurations(): Promise<readonly AgentConfiguration[]>
  saveAgentConfiguration(roleId: string, patch: AgentConfigurationPatch): Promise<AgentConfiguration>
  removeAgentConfiguration(roleId: string): Promise<boolean>
  selectAgentSystemDocument(roleId: string): Promise<AgentConfiguration>
  clearAgentSystemDocument(roleId: string): Promise<AgentConfiguration>
  listMcpConfigurations(): Promise<readonly McpConfiguration[]>
  saveMcpConfiguration(input: McpConfigurationInput): Promise<McpConfiguration>
  removeMcpConfiguration(id: string): Promise<boolean>
  getCloudSkillsCatalog(): Promise<string>
  setCloudSkillsCatalog(value: string): Promise<void>
  watchSource(sourceId: string): Promise<void>
  onSnapshot(listener: (snapshot: StateSnapshot) => void): () => void
  getEngineHandshake(): Promise<EngineHandshake>
  sendCommand(command: OperatorCommand): Promise<CommandReceipt>
}

export const EMPTY_CAPABILITIES: CapabilityMap = {
  requirements: false,
  planConfirmation: false,
  pauseAtSafePoint: false,
  resume: false,
  stop: false,
  keepMemory: false,
  forkFromCheckpoint: false,
  appendPrompt: false,
  insertModule: false
}
