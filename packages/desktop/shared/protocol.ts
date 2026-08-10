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
