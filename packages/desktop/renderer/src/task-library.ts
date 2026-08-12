import type { RoleSpec } from '@codentum/contracts'
import type { McpServiceProjection } from '../../shared/protocol'

export type TaskSessionStatus = 'draft' | 'submitted'
export type AccessMode = 'read_only' | 'workspace_write' | 'full_access'

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
  return /(?:测试|验证|验收|集成|test|tests|testing|verify|validation|integration|qa)/iu.test(`${task.title} ${task.preview}`)
}

export function searchTaskSessions(tasks: readonly TaskSession[], query: string): readonly TaskSession[] {
  const normalized = query.trim().toLocaleLowerCase('zh-CN')
  if (normalized === '') return tasks
  return tasks.filter((task) =>
    `${task.title} ${task.preview} ${task.attachmentNames.join(' ')}`.toLocaleLowerCase('zh-CN').includes(normalized)
  )
}

export interface ResourceOption {
  readonly id: string
  readonly label: string
  readonly detail: string
  readonly availability: 'available' | 'pending_runtime'
}

export const PLUGIN_OPTIONS: readonly ResourceOption[] = [
  { id: 'local-files', label: '本地文件', detail: '直接引用原始位置，提交前校验内容', availability: 'available' },
  { id: 'git', label: 'Git', detail: '由 WorkerRuntime 在隔离 worktree 中使用', availability: 'available' },
  { id: 'browser', label: '浏览器', detail: '浏览器 MCP 服务尚未连接；不会假装工具已可调用', availability: 'pending_runtime' }
]

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
  'cost-governance': '成本治理',
  evolution: '能力进化'
}

const MCP_PLUGIN_ORDER = ['filesystem', 'git', 'browser'] as const
const MCP_PLUGIN_IDS: Readonly<Record<(typeof MCP_PLUGIN_ORDER)[number], string>> = {
  filesystem: 'local-files',
  git: 'git',
  browser: 'browser'
}
const MCP_PLUGIN_LABELS: Readonly<Record<(typeof MCP_PLUGIN_ORDER)[number], string>> = {
  filesystem: '本地文件',
  git: 'Git',
  browser: '浏览器'
}

function mcpPluginDetail(service: McpServiceProjection): string {
  if (service.status === 'connected') {
    const toolSummary = service.tools.length === 0 ? '暂无工具' : `${service.tools.length} 个工具`
    return `${service.name} MCP 已投影：${toolSummary}；实际权限仍由 RoleSpec、ToolSurface 与 Guardian 收紧`
  }
  if (service.error !== undefined && service.error.trim() !== '') return service.error
  if (service.authentication === 'missing') return `${service.name} MCP 缺少凭据，工具暂不可调用`
  return `${service.name} MCP 当前${service.status === 'connecting' ? '连接中' : service.status === 'error' ? '连接错误' : '未连接'}，工具暂不可调用`
}

export function pluginOptionsFromMcpServices(services: readonly McpServiceProjection[] | undefined): readonly ResourceOption[] {
  if (services === undefined || services.length === 0) return PLUGIN_OPTIONS
  const projected = new Map(services.map((service) => [service.id, service]))
  return MCP_PLUGIN_ORDER.map((serviceId) => {
    const service = projected.get(serviceId)
    if (service === undefined) {
      return {
        id: MCP_PLUGIN_IDS[serviceId],
        label: MCP_PLUGIN_LABELS[serviceId],
        detail: `${MCP_PLUGIN_LABELS[serviceId]} MCP 未出现在当前项目投影中，工具暂不可调用`,
        availability: 'pending_runtime' as const
      }
    }
    return {
      id: MCP_PLUGIN_IDS[serviceId],
      label: MCP_PLUGIN_LABELS[serviceId],
      detail: mcpPluginDetail(service),
      availability: service.status === 'connected' ? 'available' as const : 'pending_runtime' as const
    }
  })
}

export function skillOptionsFromRoles(roles: readonly RoleSpec[] | undefined): readonly ResourceOption[] {
  const projected = new Map<string, { roles: Set<string>; active: boolean }>()
  for (const role of roles ?? []) {
    for (const skill of role.skills ?? []) {
      const entry = projected.get(skill.id) ?? { roles: new Set<string>(), active: false }
      entry.roles.add(role.id)
      if (skill.state === undefined || skill.state === 'active') entry.active = true
      projected.set(skill.id, entry)
    }
  }
  if (projected.size === 0) return SKILL_OPTIONS
  return [...projected.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([id, entry]) => ({
      id,
      label: SKILL_LABELS[id] ?? id,
      detail: `B RoleSpec 已绑定：${[...entry.roles].sort().join('、')}`,
      availability: entry.active ? 'available' : 'pending_runtime'
    }))
}

const TASK_STORAGE_KEY = 'codentum.desktop.task-sessions.v1'
const PREFERENCE_STORAGE_KEY = 'codentum.desktop.workbench-preferences.v1'
const DEFAULT_CONTEXT: TaskContextSelection = {
  accessMode: 'workspace_write',
  pluginIds: ['local-files', 'git'],
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
    context
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
    context: { ...DEFAULT_CONTEXT, accessMode: preferences.defaultAccessMode }
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
