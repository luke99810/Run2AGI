import type { PacketState, RoleId, WorkPacket } from '@codentum/contracts'
import type {
  CapabilityMap,
  EngineCapability,
  OperatorAction,
  OperatorCommand,
  StateSnapshot,
  WorkerEventProjection,
  WorkerProjection
} from '../../shared/protocol'

export type NavigationKey =
  | 'home'
  | 'execution'
  | 'waves'
  | 'dependency'
  | 'cost'
  | 'evidence'
  | 'roles'
  | 'delivery'
  | 'conversations'
  | 'plugins'
  | 'knowledge'
  | 'skills'
  | 'settings'
  | 'help'

export interface NavigationItem {
  readonly id: NavigationKey
  readonly label: string
  readonly icon: string
}

export const NAVIGATION: readonly NavigationItem[] = [
  { id: 'home', label: '新任务', icon: 'plus' },
  { id: 'execution', label: '执行中心', icon: 'pulse' },
  { id: 'conversations', label: '对话', icon: 'chat' },
  { id: 'plugins', label: '插件', icon: 'plug' },
  { id: 'knowledge', label: '知识库', icon: 'book' },
  { id: 'skills', label: 'Skills', icon: 'spark' },
  { id: 'waves', label: '依赖波次', icon: 'waves' },
  { id: 'dependency', label: '依赖关系', icon: 'graph' },
  { id: 'cost', label: '成本', icon: 'wallet' },
  { id: 'evidence', label: '证据与审计', icon: 'shield' },
  { id: 'roles', label: '研发团队', icon: 'people' },
  { id: 'delivery', label: '集成与验证', icon: 'package' }
] as const

export const PACKET_STATES: readonly PacketState[] = [
  'pending',
  'ready',
  'running',
  'blocked',
  'review',
  'accepted',
  'rejected',
  'abandoned'
] as const

export const PACKET_STATE_LABELS: Readonly<Record<PacketState, string>> = {
  pending: '等待依赖',
  ready: '可以开始',
  running: '执行中',
  blocked: '受阻',
  review: '待检查',
  accepted: '已通过',
  rejected: '需返工',
  abandoned: '已结束'
}

export const ROLE_LABELS: Readonly<Record<RoleId, string>> = {
  intake: '产品需求经理',
  architect: '技术架构师',
  planner: '研发计划师',
  qa: '测试专家',
  coder: '开发专家',
  helper: '技术诊断专家',
  reviewer: '独立评审专家',
  integrator: '集成交付专家',
  manager: '研发经理',
  evolver: '效能进化专家',
  guardian: '安全守护'
}

export interface RoleRosterEntry {
  readonly id: RoleId
  readonly summary: string
}

export const ROLE_ROSTER: readonly RoleRosterEntry[] = [
  { id: 'intake', summary: '与你澄清目标、用户和验收场景，形成产品 brief；不替你拍板产品取舍。' },
  { id: 'architect', summary: '验证技术选型，定义契约、边界和 ADR，让多个开发 Worker 可以安全并行。' },
  { id: 'planner', summary: '把计划拆成可执行 WorkPacket，计算依赖、并行度、关键路径和预算。' },
  { id: 'qa', summary: '先于实现编写验收测试并运行测试矩阵，防止只交代码、不交证据。' },
  { id: 'coder', summary: '按任务加载前端、后端、桌面或数据 Skills；多个 Worker 在各自授权路径内实现和自测。' },
  { id: 'helper', summary: '以全库只读视野诊断卡点，给出根因假说、排除路径和建议，不代替开发写代码。' },
  { id: 'reviewer', summary: '与开发使用不同模型进行对抗评审，只提交 findings，不修改被评审代码。' },
  { id: 'integrator', summary: '按冲突风险合入、守住绿线，负责打包、部署验证和回滚。' },
  { id: 'manager', summary: '调度状态机、汇总进度、仲裁冲突，只在需要决策时集中向你汇报。' },
  { id: 'evolver', summary: '复盘失败，提出 Skill 和策略改进，并通过影子回放验证是否真的更好。' },
  { id: 'guardian', summary: '不使用 LLM 的权限和策略守门；发现越权或策略违规时立即阻断。' }
] as const

const ACTION_CAPABILITIES: Readonly<Record<OperatorAction, EngineCapability>> = {
  submit_requirement: 'requirements',
  confirm_plan: 'planConfirmation',
  pause_at_safe_point: 'pauseAtSafePoint',
  resume: 'resume',
  stop: 'stop',
  stop_keep_memory: 'keepMemory',
  fork_from_checkpoint: 'forkFromCheckpoint',
  append_prompt: 'appendPrompt',
  insert_module: 'insertModule'
}

export function hasCapability(capabilities: CapabilityMap, action: OperatorAction): boolean {
  return capabilities[ACTION_CAPABILITIES[action]]
}

export function sameProjectPath(left: string | undefined, right: string | undefined): boolean {
  if (left === undefined || right === undefined) return false
  return normalizeProjectPath(left) === normalizeProjectPath(right)
}

function normalizeProjectPath(path: string): string {
  let normalized = path.replace(/\\/gu, '/')
  while (normalized.length > 1 && normalized.endsWith('/') && !/^[a-zA-Z]:\/$/u.test(normalized)) {
    normalized = normalized.slice(0, -1)
  }
  return /^[a-zA-Z]:\//u.test(normalized) || normalized.startsWith('//')
    ? normalized.toLocaleLowerCase('en-US')
    : normalized
}

export interface CommandDraft {
  readonly commandId: string
  readonly runId: string
  readonly expectedRevision: number
  readonly agentId: string
  readonly packetId?: string
  readonly moduleId?: string
  readonly action: OperatorAction
  readonly payload?: Readonly<Record<string, unknown>>
  readonly requestedAt: string
}

export function createOperatorCommand(draft: CommandDraft): OperatorCommand {
  return {
    commandId: draft.commandId,
    runId: draft.runId,
    expectedRevision: draft.expectedRevision,
    target: {
      agentId: draft.agentId,
      ...(draft.packetId === undefined ? {} : { packetId: draft.packetId }),
      ...(draft.moduleId === undefined ? {} : { moduleId: draft.moduleId })
    },
    action: draft.action,
    payload: draft.payload ?? {},
    requestedAt: draft.requestedAt
  }
}

export function roleLabel(role: string): string {
  return role in ROLE_LABELS ? ROLE_LABELS[role as RoleId] : role
}

export function formatCny(value: number): string {
  return new Intl.NumberFormat('zh-CN', {
    style: 'currency',
    currency: 'CNY',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  }).format(value)
}

export function shortPacketId(id: string): string {
  return id.length > 14 ? `${id.slice(0, 6)}…${id.slice(-5)}` : id
}

export function packetTitle(packet: WorkPacket): string {
  const firstPath = packet.ownsPaths[0]
  if (firstPath !== undefined) return firstPath
  return `${packet.kind} · ${shortPacketId(packet.id)}`
}

export interface DependencyWaves {
  readonly waves: readonly (readonly string[])[]
  readonly unresolved: readonly string[]
}

export function buildDependencyWaves(snapshot: Pick<StateSnapshot, 'graph' | 'packets'>): DependencyWaves {
  const nodeIds = new Set<string>()
  for (const packet of snapshot.packets) nodeIds.add(packet.id)
  for (const node of snapshot.graph?.dependency.nodes ?? []) nodeIds.add(node)

  const incoming = new Map<string, Set<string>>()
  const outgoing = new Map<string, Set<string>>()
  for (const id of nodeIds) {
    incoming.set(id, new Set())
    outgoing.set(id, new Set())
  }

  const explicitEdges = snapshot.graph?.dependency.edges ?? []
  if (explicitEdges.length > 0) {
    for (const edge of explicitEdges) {
      nodeIds.add(edge.from)
      nodeIds.add(edge.to)
      if (!incoming.has(edge.from)) incoming.set(edge.from, new Set())
      if (!incoming.has(edge.to)) incoming.set(edge.to, new Set())
      if (!outgoing.has(edge.from)) outgoing.set(edge.from, new Set())
      if (!outgoing.has(edge.to)) outgoing.set(edge.to, new Set())
      incoming.get(edge.to)?.add(edge.from)
      outgoing.get(edge.from)?.add(edge.to)
    }
  } else {
    for (const packet of snapshot.packets) {
      for (const dependency of packet.deps) {
        incoming.get(packet.id)?.add(dependency)
        outgoing.get(dependency)?.add(packet.id)
      }
    }
  }

  const remaining = new Set(nodeIds)
  const waves: string[][] = []
  while (remaining.size > 0) {
    const ready = [...remaining]
      .filter((id) => [...(incoming.get(id) ?? [])].every((dependency) => !remaining.has(dependency)))
      .sort((left, right) => left.localeCompare(right))
    if (ready.length === 0) break
    waves.push(ready)
    for (const id of ready) remaining.delete(id)
  }
  return { waves, unresolved: [...remaining].sort((left, right) => left.localeCompare(right)) }
}

export type ModuleState = 'waiting' | 'running' | 'completed' | 'failed' | 'unknown'

export interface WorkerModule {
  readonly id: string
  readonly label: string
  readonly state: ModuleState
  readonly firstSeq: number
  readonly lastEventAt: string
}

function readString(payload: Readonly<Record<string, unknown>>, keys: readonly string[]): string | undefined {
  for (const key of keys) {
    const value = payload[key]
    if (typeof value === 'string' && value.trim() !== '') return value
  }
  return undefined
}

function eventModule(event: WorkerEventProjection): { readonly id: string; readonly label: string } | undefined {
  const id = readString(event.payload, ['moduleId', 'module_id', 'module'])
  if (id === undefined) return undefined
  const label = readString(event.payload, ['moduleLabel', 'module_label', 'label', 'title']) ?? id
  return { id, label }
}

function eventModuleState(event: WorkerEventProjection): ModuleState {
  const explicit = readString(event.payload, ['moduleState', 'module_state', 'state', 'status'])?.toLowerCase()
  if (explicit === 'completed' || explicit === 'complete' || explicit === 'applied' || explicit === 'succeeded') return 'completed'
  if (explicit === 'failed' || explicit === 'rejected' || explicit === 'error') return 'failed'
  if (explicit === 'running' || explicit === 'started' || explicit === 'active') return 'running'
  if (explicit === 'waiting' || explicit === 'pending' || explicit === 'queued') return 'waiting'
  const kind = event.kind.toLowerCase()
  if (kind.includes('complete') || kind.includes('finish') || kind.includes('applied')) return 'completed'
  if (kind.includes('fail') || kind.includes('error') || kind.includes('reject')) return 'failed'
  if (kind.includes('start') || kind.includes('running') || kind.includes('enter')) return 'running'
  if (kind.includes('queue') || kind.includes('wait')) return 'waiting'
  return 'unknown'
}

export function projectWorkerModules(worker: WorkerProjection): readonly WorkerModule[] {
  const modules = new Map<string, WorkerModule>()
  const orderedEvents = [...worker.events].sort((left, right) => left.seq - right.seq)
  for (const event of orderedEvents) {
    const observed = eventModule(event)
    if (observed === undefined) continue
    const existing = modules.get(observed.id)
    modules.set(observed.id, {
      id: observed.id,
      label: observed.label,
      state: eventModuleState(event),
      firstSeq: existing?.firstSeq ?? event.seq,
      lastEventAt: event.at
    })
  }
  if (worker.currentModule !== undefined) {
    const existing = modules.get(worker.currentModule)
    const currentState: ModuleState = worker.state === 'failed'
      ? 'failed'
      : worker.state === 'completed'
        ? 'completed'
        : worker.state === 'running' || worker.state === 'starting'
          ? 'running'
          : worker.state === 'waiting'
            ? 'waiting'
            : 'unknown'
    modules.set(worker.currentModule, {
      id: worker.currentModule,
      label: existing?.label ?? worker.currentModule,
      state: currentState,
      firstSeq: existing?.firstSeq ?? Number.MAX_SAFE_INTEGER,
      lastEventAt: existing?.lastEventAt ?? worker.startedAt ?? ''
    })
  }
  return [...modules.values()].sort((left, right) => left.firstSeq - right.firstSeq)
}

export function packetCounts(packets: readonly WorkPacket[]): Readonly<Record<PacketState, number>> {
  const counts: Record<PacketState, number> = {
    pending: 0,
    ready: 0,
    running: 0,
    blocked: 0,
    review: 0,
    accepted: 0,
    rejected: 0,
    abandoned: 0
  }
  for (const packet of packets) counts[packet.state] += 1
  return counts
}

export function sourceModeLabel(snapshot: StateSnapshot | null): string {
  if (snapshot === null) return '尚未读取'
  return snapshot.source.kind === 'fixture' ? '演示快照' : '本地项目'
}

const MISSING_STATE_DIRECTORY_PREFIX = '[missing] State directory is unavailable:'

export function warningsForDisplay(warnings: readonly string[]): readonly string[] {
  const missingStateDirectory = warnings.find((warning) => warning.startsWith(MISSING_STATE_DIRECTORY_PREFIX))
  if (missingStateDirectory === undefined) return warnings
  return [
    missingStateDirectory,
    ...warnings.filter((warning) => !warning.startsWith('[missing]') && warning !== missingStateDirectory)
  ]
}
