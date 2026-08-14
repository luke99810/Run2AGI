import type {
  BudgetFile,
  DecisionRecord,
  Evidence,
  GraphFile,
  KnowledgeFile,
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

export interface AgentConfiguration {
  readonly roleId: string
  readonly name?: string
  readonly custom?: boolean
  readonly systemPrompt: string
  readonly systemDocumentName?: string
  readonly apiKeyConfigured: boolean
  readonly updatedAt: string
}

export interface AgentConfigurationPatch {
  readonly name?: string
  readonly systemPrompt?: string
  readonly apiKey?: string
  readonly clearApiKey?: boolean
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
  readonly mcpServices: readonly McpServiceProjection[]
  readonly workers: readonly WorkerProjection[]
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
