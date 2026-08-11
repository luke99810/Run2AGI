import { useMemo, useState, type ReactNode } from 'react'
import type { StateSnapshot } from '../shared/protocol'
import { Icon, PageHeader } from '../panels/Common'
import {
  KNOWLEDGE_OPTIONS,
  PLUGIN_OPTIONS,
  searchTaskSessions,
  SKILL_OPTIONS,
  toggleSelection,
  type ResourceOption,
  type TaskContextSelection,
  type TaskSession,
  type WorkbenchPreferences
} from '../renderer/src/task-library'

export function ConversationsView({ tasks, activeTaskId, onSelectTask, onNewTask }: {
  readonly tasks: readonly TaskSession[]
  readonly activeTaskId: string | null
  readonly onSelectTask: (taskId: string) => void
  readonly onNewTask: () => void
}): ReactNode {
  const [query, setQuery] = useState('')
  const visible = useMemo(() => searchTaskSessions(tasks, query), [query, tasks])

  return (
    <main className="page conversation-page">
      <PageHeader
        title="对话"
        description="每个对话使用独立 taskId、草稿和附件区；切换对话不会覆盖其他任务。"
        actions={<button type="button" className="primary-button" onClick={onNewTask}><Icon name="plus" size={18} />新对话</button>}
      />
      <label className="page-search"><Icon name="search" size={18} /><input autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索关键词、需求内容或文件名" /></label>
      <div className="conversation-list" aria-live="polite">
        {visible.length === 0 ? (
          <div className="plain-empty"><strong>没有匹配的对话</strong><span>换个关键词，或新建一个独立任务。</span></div>
        ) : visible.map((task) => (
          <button type="button" className={task.id === activeTaskId ? 'active' : ''} key={task.id} onClick={() => onSelectTask(task.id)}>
            <Icon name="chat" size={18} />
            <span><strong>{task.title}</strong><small>{task.preview || '尚未输入需求'}</small></span>
            <em>{task.status === 'submitted' ? '已提交' : '草稿'}</em>
            <time dateTime={task.updatedAt}>{new Date(task.updatedAt).toLocaleString('zh-CN')}</time>
          </button>
        ))}
      </div>
    </main>
  )
}

function resourceCopy(kind: 'plugins' | 'knowledge' | 'skills'): { title: string; description: string; icon: 'plug' | 'book' | 'spark'; options: readonly ResourceOption[]; key: 'pluginIds' | 'knowledgeIds' | 'skillIds' } {
  if (kind === 'plugins') return { title: '插件', description: '为当前任务声明可用工具。标记“待接入”的项目会随需求发送，但不会假装已经生效。', icon: 'plug', options: PLUGIN_OPTIONS, key: 'pluginIds' }
  if (kind === 'knowledge') return { title: '知识库', description: '选择随当前需求提交的上下文来源。本地任务摘要会进入请求，但不冒充尚未接入的 MemoryIndex 语义检索。', icon: 'book', options: KNOWLEDGE_OPTIONS, key: 'knowledgeIds' }
  return { title: 'Skills', description: '为当前任务声明需要的专业能力；最终 Skill 加载由 B 的 RoleSpec 与 ToolSurface 决定。', icon: 'spark', options: SKILL_OPTIONS, key: 'skillIds' }
}

export function ResourceLibraryView({ kind, task, onContextChange }: {
  readonly kind: 'plugins' | 'knowledge' | 'skills'
  readonly task: TaskSession | undefined
  readonly onContextChange: (context: TaskContextSelection) => void
}): ReactNode {
  const copy = resourceCopy(kind)
  const selected = task?.context[copy.key] ?? []
  return (
    <main className="page resource-page">
      <PageHeader title={copy.title} description={copy.description} />
      {task === undefined ? <div className="plain-empty"><strong>正在准备任务</strong></div> : (
        <div className="resource-list">
          {copy.options.map((option) => {
            const checked = selected.includes(option.id)
            return (
              <label key={option.id} className={checked ? 'selected' : ''}>
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => onContextChange({ ...task.context, [copy.key]: toggleSelection(selected, option.id) })}
                />
                <span className="resource-icon"><Icon name={copy.icon} size={19} /></span>
                <span><strong>{option.label}</strong><small>{option.detail}</small></span>
                <em>{option.availability === 'available' ? '可用' : '待执行层接入'}</em>
              </label>
            )
          })}
        </div>
      )}
    </main>
  )
}

export function SettingsView({ preferences, onChange }: {
  readonly preferences: WorkbenchPreferences
  readonly onChange: (preferences: WorkbenchPreferences) => void
}): ReactNode {
  return (
    <main className="page settings-page">
      <PageHeader title="设置" description="设置只影响以后新建的任务；已有任务保持各自独立配置。" />
      <section className="settings-section">
        <header><strong>默认访问权限</strong><span>这是请求上限，实际权限仍由 RoleSpec 和 Guardian 收紧。</span></header>
        <div className="settings-options">
          {([
            ['read_only', '只读', '仅分析项目和附件'],
            ['workspace_write', '工作区写入', '仅在授权路径内修改'],
            ['full_access', '完全访问', '请求更高权限，仍需策略批准']
          ] as const).map(([id, label, detail]) => (
            <label key={id}>
              <input type="radio" name="default-access" checked={preferences.defaultAccessMode === id} onChange={() => onChange({ defaultAccessMode: id })} />
              <span><strong>{label}</strong><small>{detail}</small></span>
            </label>
          ))}
        </div>
      </section>
      <section className="settings-section">
        <header><strong>界面</strong><span>当前固定使用灰白工作台，避免用颜色代替状态文字。</span></header>
        <div className="settings-readonly"><span>主题</span><strong>灰白</strong></div>
      </section>
    </main>
  )
}

export function HelpView({ snapshot, onOpenAgents }: {
  readonly snapshot: StateSnapshot | null
  readonly onOpenAgents: () => void
}): ReactNode {
  const [issue, setIssue] = useState('')
  const [copyStatus, setCopyStatus] = useState<string | null>(null)

  async function copySupportBundle(): Promise<void> {
    const diagnostics = [
      `项目：${snapshot?.source.label ?? '未选择'}`,
      `状态版本：${snapshot?.revision ?? '无'}`,
      `任务数：${snapshot?.packets.length ?? 0}`,
      `Worker 数：${snapshot?.workers.length ?? 0}`,
      `问题：${issue.trim() || '未填写'}`
    ].join('\n')
    try {
      await navigator.clipboard.writeText(diagnostics)
      setCopyStatus('诊断信息已复制，可粘贴给客服。')
    } catch {
      setCopyStatus('系统未允许访问剪贴板，请手动复制问题描述。')
    }
  }

  return (
    <main className="page help-page">
      <PageHeader title="帮助" description="管理 Agent 状态，或生成不含凭证的本地诊断信息。" />
      <section className="help-section">
        <span className="help-icon"><Icon name="people" size={21} /></span>
        <div><strong>Agent 管理</strong><p>{snapshot?.roles.length ?? 0} 个角色配置 · {snapshot?.workers.length ?? 0} 个 Worker 投影</p></div>
        <button type="button" className="secondary-button" onClick={onOpenAgents}>打开 Agent 管理</button>
      </section>
      <section className="support-section">
        <header><span className="help-icon"><Icon name="help" size={21} /></span><div><strong>客服接入</strong><p>当前未配置远端客服端点，内容不会自动上传。</p></div></header>
        <label><span>问题描述</span><textarea value={issue} onChange={(event) => setIssue(event.target.value)} placeholder="描述遇到的问题" rows={5} /></label>
        <div><button type="button" className="secondary-button" onClick={() => void copySupportBundle()}>复制诊断信息</button><span role="status">{copyStatus}</span></div>
      </section>
    </main>
  )
}
