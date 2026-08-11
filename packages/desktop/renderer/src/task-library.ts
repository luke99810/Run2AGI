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
  { id: 'browser', label: '浏览器', detail: '等待 B 将浏览器工具加入 ToolSurface', availability: 'pending_runtime' }
]

export const KNOWLEDGE_OPTIONS: readonly ResourceOption[] = [
  { id: 'project-knowledge', label: '项目知识库', detail: '读取当前项目 .codentum 知识投影', availability: 'available' },
  { id: 'task-history', label: '本地任务摘要', detail: '随需求附带本机历史任务摘要；当前不是 MemoryIndex 语义检索', availability: 'available' },
  { id: 'team-memory', label: '团队记忆', detail: '等待 A/B 提供 MemoryIndex 查询端点', availability: 'pending_runtime' }
]

export const SKILL_OPTIONS: readonly ResourceOption[] = [
  { id: 'requirement-clarify', label: '需求澄清', detail: 'Intake · 需求槽位与假设确认', availability: 'pending_runtime' },
  { id: 'contract-design', label: '契约设计', detail: 'Architect · API、数据与边界契约', availability: 'pending_runtime' },
  { id: 'feasibility-probe', label: '可行性探测', detail: 'Architect · 安装、编译与冲突探测', availability: 'pending_runtime' },
  { id: 'boundary-score', label: '边界评分', detail: 'Architect · 并行潜力与耦合度评估', availability: 'pending_runtime' },
  { id: 'packet-split', label: '任务拆分', detail: 'Planner · 生成可执行 WorkPacket', availability: 'pending_runtime' },
  { id: 'virtual-schedule', label: '虚拟排程', detail: 'Planner · 关键路径、冲突与死锁检查', availability: 'pending_runtime' },
  { id: 'acceptance-authoring', label: '验收用例编写', detail: 'QA · 先于实现编写验收测试', availability: 'pending_runtime' },
  { id: 'adversarial-review', label: '对抗评审', detail: 'Reviewer · 反证式代码评审', availability: 'pending_runtime' },
  { id: 'diagnose-assist', label: '诊断协助', detail: 'Helper · 只读定位失败根因', availability: 'pending_runtime' },
  { id: 'integrate-merge', label: '集成合并', detail: 'Integrator · 合并、冒烟与回退', availability: 'pending_runtime' },
  { id: 'dependency-policy-check', label: '依赖策略检查', detail: 'Global · 新依赖准入与替代建议', availability: 'pending_runtime' },
  { id: 'rule-conformance-check', label: '规则符合性检查', detail: 'Global · 规则集与变更一致性', availability: 'pending_runtime' },
  { id: 'gate-check', label: '门禁检查', detail: 'Global · 阶段转换前执行门禁', availability: 'pending_runtime' },
  { id: 'context-recipe', label: '上下文配方', detail: 'Global · 按角色与预算组装上下文', availability: 'pending_runtime' },
  { id: 'cost-estimate', label: '成本估算', detail: 'Global · 生成分阶段成本区间', availability: 'pending_runtime' },
  { id: 'provisioning-collect', label: '交付配置收集', detail: 'Integrator · 收集并校验交付配置', availability: 'pending_runtime' },
  { id: 'secret-leak-scan', label: '凭证泄漏扫描', detail: 'Global · 交付前不可豁免扫描', availability: 'pending_runtime' },
  { id: 'deploy-verify-checklist', label: '部署验证清单', detail: 'Integrator · 版本、健康与冒烟验证', availability: 'pending_runtime' },
  { id: 'cold-start-verify', label: '冷启动验证', detail: 'Global · 零缓存环境复现验证', availability: 'pending_runtime' },
  { id: 'canary-release', label: '金丝雀发布', detail: 'Integrator · 分阶段发布与自动回滚', availability: 'pending_runtime' },
  { id: 'failure-cluster', label: '失败聚类', detail: 'Evolver · 聚类 Trace 中的失败模式', availability: 'pending_runtime' },
  { id: 'experience-distill', label: '经验蒸馏', detail: 'Evolver · 证伪后沉淀可复用经验', availability: 'pending_runtime' },
  { id: 'shadow-replay', label: '影子回放', detail: 'Evolver · 能力变更回归验证', availability: 'pending_runtime' },
  { id: 'skill-authoring', label: 'Skill 生成', detail: 'Evolver · 生成 manifest、实现与测试', availability: 'pending_runtime' },
  { id: 'mutation-test', label: '变异测试', detail: 'QA · 验证测试是否真正有效', availability: 'pending_runtime' }
]

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
