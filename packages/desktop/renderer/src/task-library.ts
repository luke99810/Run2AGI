import type { RoleSpec } from '@codentum/contracts'
import type { McpServiceProjection, SkillProjection, StateSnapshot } from '../../shared/protocol'

export type TaskSessionStatus = 'draft' | 'submitted'
export type AccessMode = 'read_only' | 'workspace_write' | 'full_access'
export type TaskConversationKind = 'user' | 'command' | 'receipt' | 'agent' | 'evidence' | 'error'

export interface TaskConversationEntry {
  readonly id: string
  readonly kind: TaskConversationKind
  readonly at: string
  readonly text: string
  readonly commandId?: string
  readonly action?: string
  readonly packetId?: string
  readonly evidenceRefs?: readonly string[]
}

export interface TaskContextSelection {
  readonly accessMode: AccessMode
  readonly pluginIds: readonly string[]
  readonly knowledgeIds: readonly string[]
  readonly skillIds: readonly string[]
  readonly relatedTaskIds: readonly string[]
}

export interface TaskSession {
  readonly id: string
  readonly sourceId: string
  readonly title: string
  readonly preview: string
  readonly attachmentNames: readonly string[]
  readonly status: TaskSessionStatus
  readonly createdAt: string
  readonly updatedAt: string
  readonly context: TaskContextSelection
  readonly conversation: readonly TaskConversationEntry[]
}

export interface TaskHistoryEntry {
  readonly taskId: string
  readonly title: string
  readonly summary: string
  readonly status: TaskSessionStatus
  readonly updatedAt: string
}

export interface WorkbenchPreferences {
  readonly defaultAccessMode: AccessMode
}

export function taskRequestsValidation(task: Pick<TaskSession, 'title' | 'preview'>): boolean {
  return /(?:测试|验证|验收|集成|打包|交付|构建|test|tests|testing|verify|validation|integration|package|delivery|build|release|qa)/iu.test(`${task.title} ${task.preview}`)
}

export function searchTaskSessions(tasks: readonly TaskSession[], query: string, snapshot?: StateSnapshot | null): readonly TaskSession[] {
  const normalized = query.trim().toLocaleLowerCase('zh-CN')
  if (normalized === '') return tasks
  return tasks.filter((task) => {
    const timeline = taskConversationEntries(task, snapshot)
    const text = [task.title, task.preview, ...task.attachmentNames, ...timeline.flatMap((entry) => [entry.text, entry.action ?? '', entry.packetId ?? '', ...(entry.evidenceRefs ?? [])])].join(' ')
    return text.toLocaleLowerCase('zh-CN').includes(normalized)
  })
}

export function appendTaskConversation(task: TaskSession, entry: TaskConversationEntry): TaskSession {
  const conversation = [...task.conversation, entry].slice(-500)
  return { ...task, conversation, updatedAt: entry.at }
}

export function taskConversationEntries(task: TaskSession, snapshot?: StateSnapshot | null): readonly TaskConversationEntry[] {
  if (snapshot === undefined || snapshot === null) return task.conversation
  const requirements = snapshot.requirements.filter((requirement) => requirement.taskId === task.id)
  const packetIds = new Set(requirements.map((requirement) => requirement.packetId))
  const runtime: TaskConversationEntry[] = requirements.map((requirement) => ({
    id: `requirement:${requirement.commandId}`,
    kind: 'receipt',
    at: requirement.submittedAt,
    text: `需求已由引擎登记为 ${requirement.packetId}`,
    commandId: requirement.commandId,
    action: 'submit_requirement',
    packetId: requirement.packetId
  }))
  for (const worker of snapshot.workers) {
    if (!packetIds.has(worker.packetId)) continue
    for (const event of worker.events) {
      runtime.push({
        id: `worker:${worker.workerId}:${event.seq}`,
        kind: 'agent',
        at: event.at,
        text: `${worker.role}/${event.kind}: ${payloadText(event.payload)}`,
        action: event.kind,
        packetId: worker.packetId
      })
    }
  }
  for (const evidence of snapshot.evidence) {
    if (!packetIds.has(evidence.packetId)) continue
    runtime.push({
      id: `evidence:${evidence.ref}`,
      kind: 'evidence',
      at: evidence.at,
      text: `${evidence.kind}/${evidence.verdict}${evidence.gate === undefined ? '' : ` · ${evidence.gate}`}${evidence.artifacts.length === 0 ? '' : ` · ${evidence.artifacts.join('、')}`}`,
      packetId: evidence.packetId,
      evidenceRefs: [evidence.ref]
    })
  }
  const deduplicated = new Map<string, TaskConversationEntry>()
  for (const entry of [...task.conversation, ...runtime]) deduplicated.set(entry.id, entry)
  return [...deduplicated.values()].sort((left, right) => left.at.localeCompare(right.at) || left.id.localeCompare(right.id))
}

function payloadText(payload: Readonly<Record<string, unknown>>): string {
  try {
    const text = JSON.stringify(payload)
    return text.length > 2_000 ? `${text.slice(0, 2_000)}…` : text
  } catch {
    return '[无法序列化的事件 payload]'
  }
}

export interface ResourceOption {
  readonly id: string
  readonly label: string
  readonly detail: string
  readonly availability: 'available' | 'pending_runtime'
  readonly projection?: SkillProjection
  readonly mcpProjection?: McpServiceProjection
}

export const PLUGIN_OPTIONS: readonly ResourceOption[] = []

export function pluginOptionsFromMcp(services: readonly McpServiceProjection[] | undefined): readonly ResourceOption[] {
  return (services ?? [])
    .filter((service) => service.category === 'third-party-app')
    .sort((left, right) => left.name.localeCompare(right.name, 'zh-CN'))
    .map((service) => ({
      id: service.id,
      label: service.name,
      detail: service.purpose ?? `${service.transport} 第三方应用服务`,
      availability: service.status === 'connected' ? 'available' : 'pending_runtime',
      mcpProjection: service
    }))
}

export const KNOWLEDGE_OPTIONS: readonly ResourceOption[] = [
  { id: 'project-knowledge', label: '项目知识库', detail: '读取当前项目 .codentum 知识投影', availability: 'available' },
  { id: 'task-history', label: '本地任务摘要', detail: '随需求附带本机历史任务摘要；当前不是 MemoryIndex 语义检索', availability: 'available' },
  { id: 'team-memory', label: '团队记忆', detail: '等待 A/B 提供 MemoryIndex 查询端点', availability: 'pending_runtime' }
]

export const SKILL_OPTIONS: readonly ResourceOption[] = [
  { id: 'requirements', label: '需求澄清', detail: '请求 Intake 加载需求澄清技能', availability: 'pending_runtime' },
  { id: 'architecture', label: '架构边界', detail: '请求 Architect 加载架构边界技能', availability: 'pending_runtime' },
  { id: 'planning', label: '研发计划', detail: '请求 Planner/Manager 加载计划技能', availability: 'pending_runtime' },
  { id: 'frontend', label: '前端实现', detail: '请求 Coder 加载前端实现技能', availability: 'pending_runtime' },
  { id: 'backend', label: '后端实现', detail: '请求 Coder/Helper 加载后端实现技能', availability: 'pending_runtime' },
  { id: 'testing', label: '测试验证', detail: '请求 QA/Coder 加载测试技能', availability: 'pending_runtime' },
  { id: 'debugging', label: '问题诊断', detail: '请求 Helper/Coder 加载诊断技能', availability: 'pending_runtime' },
  { id: 'review', label: '代码评审', detail: '请求 Reviewer 加载评审技能', availability: 'pending_runtime' },
  { id: 'security', label: '安全审计', detail: '请求 Guardian/Reviewer 加载安全技能', availability: 'pending_runtime' },
  { id: 'integration', label: '集成发布', detail: '请求 Integrator 加载集成技能', availability: 'pending_runtime' },
  { id: 'delivery', label: '交付打包', detail: '请求 Integrator 加载交付打包技能', availability: 'pending_runtime' },
  { id: 'cost-governance', label: '成本治理', detail: '请求 Manager/Planner 加载成本治理技能', availability: 'pending_runtime' },
  { id: 'evolution', label: '能力进化', detail: '请求 Evolver 加载能力进化技能', availability: 'pending_runtime' }
]

const SKILL_LABELS: Readonly<Record<string, string>> = {
  requirements: '需求澄清',
  architecture: '架构边界',
  planning: '研发计划',
  frontend: '前端实现',
  backend: '后端实现',
  testing: '测试验证',
  debugging: '问题诊断',
  review: '代码评审',
  security: '安全审计',
  integration: '集成发布',
  delivery: '交付打包',
  'cost-governance': '成本治理',
  evolution: '能力进化'
}

export function skillOptionsFromRoles(
  roles: readonly RoleSpec[] | undefined,
  skills: readonly SkillProjection[] | undefined = undefined
): readonly ResourceOption[] {
  const projected = new Map<string, { roles: Set<string>; active: boolean }>()
  for (const role of roles ?? []) {
    for (const skill of role.skills ?? []) {
      const entry = projected.get(skill.id) ?? { roles: new Set<string>(), active: false }
      entry.roles.add(role.id)
      if (skill.state === undefined || skill.state === 'active') entry.active = true
      projected.set(skill.id, entry)
    }
  }
  const skillById = new Map((skills ?? []).map((skill) => [skill.id, skill]))
  const ids = new Set([...projected.keys(), ...skillById.keys()])
  if (ids.size === 0) return SKILL_OPTIONS
  return [...ids]
    .sort((left, right) => left.localeCompare(right))
    .map((id) => {
      const binding = projected.get(id)
      const projection = skillById.get(id)
      const rolesText = binding === undefined ? projection?.appliesTo.join('、') : [...binding.roles].sort().join('、')
      return {
        id,
        label: SKILL_LABELS[id] ?? id,
        detail: projection?.description ?? `B RoleSpec 已绑定：${rolesText || '未指定角色'}；等待 Skill manifest 与 SKILL.md 投影`,
        availability: projection !== undefined && (binding?.active ?? true) ? 'available' : 'pending_runtime',
        ...(projection === undefined ? {} : { projection })
      }
    })
}

const TASK_STORAGE_KEY = 'codentum.desktop.task-sessions.v1'
const PREFERENCE_STORAGE_KEY = 'codentum.desktop.workbench-preferences.v1'
const DEFAULT_CONTEXT: TaskContextSelection = {
  accessMode: 'workspace_write',
  pluginIds: [],
  knowledgeIds: ['project-knowledge', 'task-history'],
  skillIds: [],
  relatedTaskIds: []
}
const DEFAULT_PREFERENCES: WorkbenchPreferences = { defaultAccessMode: 'workspace_write' }

function storage(): Storage | null {
  return typeof window === 'undefined' ? null : window.localStorage
}

function newId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  return `00000000-0000-4000-8000-${Date.now().toString(16).padStart(12, '0').slice(-12)}`
}

function isStringArray(value: unknown): value is readonly string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string')
}

function isAccessMode(value: unknown): value is AccessMode {
  return value === 'read_only' || value === 'workspace_write' || value === 'full_access'
}

function parseConversation(value: unknown): readonly TaskConversationEntry[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((item) => {
    if (typeof item !== 'object' || item === null || Array.isArray(item)) return []
    const record = item as Record<string, unknown>
    if (
      typeof record['id'] !== 'string' ||
      !['user', 'command', 'receipt', 'agent', 'evidence', 'error'].includes(String(record['kind'])) ||
      typeof record['at'] !== 'string' ||
      typeof record['text'] !== 'string'
    ) return []
    return [{
      id: record['id'],
      kind: record['kind'] as TaskConversationKind,
      at: record['at'],
      text: record['text'],
      ...(typeof record['commandId'] === 'string' ? { commandId: record['commandId'] } : {}),
      ...(typeof record['action'] === 'string' ? { action: record['action'] } : {}),
      ...(typeof record['packetId'] === 'string' ? { packetId: record['packetId'] } : {}),
      ...(isStringArray(record['evidenceRefs']) ? { evidenceRefs: record['evidenceRefs'] } : {})
    }]
  }).slice(-500)
}

function parseContext(value: unknown): TaskContextSelection | null {
  if (typeof value !== 'object' || value === null) return null
  const record = value as Record<string, unknown>
  if (
    !isAccessMode(record['accessMode']) ||
    !isStringArray(record['pluginIds']) ||
    !isStringArray(record['knowledgeIds']) ||
    !isStringArray(record['skillIds']) ||
    !isStringArray(record['relatedTaskIds'])
  ) return null
  return {
    accessMode: record['accessMode'],
    pluginIds: [...new Set(record['pluginIds'])],
    knowledgeIds: [...new Set(record['knowledgeIds'])],
    skillIds: [...new Set(record['skillIds'])],
    relatedTaskIds: [...new Set(record['relatedTaskIds'])]
  }
}

function parseTask(value: unknown): TaskSession | null {
  if (typeof value !== 'object' || value === null) return null
  const record = value as Record<string, unknown>
  const context = parseContext(record['context'])
  if (
    typeof record['id'] !== 'string' ||
    !/^[0-9a-f-]{36}$/u.test(record['id']) ||
    typeof record['sourceId'] !== 'string' ||
    typeof record['title'] !== 'string' ||
    typeof record['preview'] !== 'string' ||
    (record['status'] !== 'draft' && record['status'] !== 'submitted') ||
    typeof record['createdAt'] !== 'string' ||
    typeof record['updatedAt'] !== 'string' ||
    context === null
  ) return null
  return {
    id: record['id'],
    sourceId: record['sourceId'],
    title: record['title'],
    preview: record['preview'],
    attachmentNames: isStringArray(record['attachmentNames']) ? [...new Set(record['attachmentNames'])] : [],
    status: record['status'],
    createdAt: record['createdAt'],
    updatedAt: record['updatedAt'],
    context,
    conversation: parseConversation(record['conversation'])
  }
}

export function loadTaskSessions(): readonly TaskSession[] {
  try {
    const raw = storage()?.getItem(TASK_STORAGE_KEY)
    if (raw === null || raw === undefined) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.map(parseTask).filter((task): task is TaskSession => task !== null)
  } catch {
    return []
  }
}

export function saveTaskSessions(tasks: readonly TaskSession[]): void {
  storage()?.setItem(TASK_STORAGE_KEY, JSON.stringify(tasks))
}

export function loadWorkbenchPreferences(): WorkbenchPreferences {
  try {
    const raw = storage()?.getItem(PREFERENCE_STORAGE_KEY)
    if (raw === null || raw === undefined) return DEFAULT_PREFERENCES
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null) return DEFAULT_PREFERENCES
    const accessMode = (parsed as Record<string, unknown>)['defaultAccessMode']
    return isAccessMode(accessMode) ? { defaultAccessMode: accessMode } : DEFAULT_PREFERENCES
  } catch {
    return DEFAULT_PREFERENCES
  }
}

export function saveWorkbenchPreferences(preferences: WorkbenchPreferences): void {
  storage()?.setItem(PREFERENCE_STORAGE_KEY, JSON.stringify(preferences))
}

export function createTaskSession(sourceId: string, preferences: WorkbenchPreferences, now = new Date()): TaskSession {
  const timestamp = now.toISOString()
  return {
    id: newId(),
    sourceId,
    title: '新任务',
    preview: '',
    attachmentNames: [],
    status: 'draft',
    createdAt: timestamp,
    updatedAt: timestamp,
    context: { ...DEFAULT_CONTEXT, accessMode: preferences.defaultAccessMode },
    conversation: []
  }
}

export function taskDraftScope(task: TaskSession): string {
  return `${task.sourceId}:task:${task.id}`
}

export function taskTitle(requirement: string): string {
  const normalized = requirement.trim().replace(/\s+/gu, ' ')
  if (normalized === '') return '新任务'
  return normalized.length > 28 ? `${normalized.slice(0, 28)}…` : normalized
}

export function updateTaskFromDraft(task: TaskSession, requirement: string, now = new Date()): TaskSession {
  const normalized = requirement.trim().replace(/\s+/gu, ' ')
  return {
    ...task,
    title: taskTitle(requirement),
    preview: normalized.slice(0, 160),
    updatedAt: now.toISOString()
  }
}

export function historyForAgent(tasks: readonly TaskSession[], activeTaskId: string): readonly TaskHistoryEntry[] {
  return tasks
    .filter((task) => task.id !== activeTaskId && task.preview !== '')
    .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt))
    .slice(0, 30)
    .map((task) => ({
      taskId: task.id,
      title: task.title,
      summary: task.preview,
      status: task.status,
      updatedAt: task.updatedAt
    }))
}

export function toggleSelection(values: readonly string[], id: string): readonly string[] {
  return values.includes(id) ? values.filter((value) => value !== id) : [...values, id]
}
