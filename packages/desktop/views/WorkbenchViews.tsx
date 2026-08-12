import { useEffect, useMemo, useState, type ReactNode } from 'react'
import type { ManagedResource, ManagedResourceKind, ManagedResourcePatch, StateSnapshot } from '../shared/protocol'
import { Icon, PageHeader } from '../panels/Common'
import {
  KNOWLEDGE_OPTIONS,
  searchTaskSessions,
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
        title="对话检索与导出"
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

function resourceCopy(kind: 'plugins' | 'knowledge' | 'skills', pluginOptions: readonly ResourceOption[], skillOptions: readonly ResourceOption[]): { title: string; description: string; icon: 'plug' | 'book' | 'spark'; options: readonly ResourceOption[]; key: 'pluginIds' | 'knowledgeIds' | 'skillIds' } {
  if (kind === 'plugins') return { title: '插件', description: '选择随当前任务使用的第三方应用连接。插件与 MCP 工具服务分开管理。', icon: 'plug', options: pluginOptions, key: 'pluginIds' }
  if (kind === 'knowledge') return { title: '知识库', description: '选择随当前需求提交的上下文来源。本地任务摘要会进入请求，但不冒充尚未接入的 MemoryIndex 语义检索。', icon: 'book', options: KNOWLEDGE_OPTIONS, key: 'knowledgeIds' }
  return { title: 'Skills', description: '为当前任务声明需要的专业能力；选项优先来自 B 投影到项目 RoleSpec 的 Skill。', icon: 'spark', options: skillOptions, key: 'skillIds' }
}

const RESOURCE_ROLES = [
  ['intake', '产品需求经理'], ['architect', '技术架构师'], ['planner', '研发计划师'],
  ['qa', '测试专家'], ['coder', '开发专家'], ['helper', '技术诊断专家'],
  ['reviewer', '代码评审专家'], ['integrator', '集成专家'], ['manager', '产品经理 Agent'],
  ['evolver', '进化专家'], ['guardian', '安全守护专家']
] as const

function managedKind(kind: 'plugins' | 'knowledge' | 'skills'): ManagedResourceKind {
  return kind === 'plugins' ? 'plugin' : kind === 'knowledge' ? 'knowledge' : 'skill'
}

export function ResourceLibraryView({
  kind,
  task,
  pluginOptions,
  skillOptions,
  snapshot,
  onContextChange,
  listManagedResources,
  selectManagedResources,
  addManagedResourceUrl,
  updateManagedResource,
  removeManagedResource
}: {
  readonly kind: 'plugins' | 'knowledge' | 'skills'
  readonly task: TaskSession | undefined
  readonly snapshot: StateSnapshot | null
  readonly pluginOptions: readonly ResourceOption[]
  readonly skillOptions: readonly ResourceOption[]
  readonly onContextChange: (context: TaskContextSelection) => void
  readonly listManagedResources: (kind?: ManagedResourceKind) => Promise<readonly ManagedResource[]>
  readonly selectManagedResources: (kind: ManagedResourceKind, sourceKind: 'file' | 'folder') => Promise<readonly ManagedResource[]>
  readonly addManagedResourceUrl: (kind: ManagedResourceKind, url: string) => Promise<ManagedResource>
  readonly updateManagedResource: (id: string, patch: ManagedResourcePatch) => Promise<ManagedResource>
  readonly removeManagedResource: (id: string) => Promise<boolean>
}): ReactNode {
  const copy = resourceCopy(kind, pluginOptions, skillOptions)
  const resourceKind = managedKind(kind)
  const selected = task?.context[copy.key] ?? []
  const [managed, setManaged] = useState<readonly ManagedResource[]>([])
  const [query, setQuery] = useState('')
  const [gitUrl, setGitUrl] = useState('')
  const [showGitForm, setShowGitForm] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setQuery('')
    setGitUrl('')
    setShowGitForm(false)
    setError(null)
  }, [kind])

  useEffect(() => {
    let alive = true
    setError(null)
    void listManagedResources(resourceKind)
      .then((items) => { if (alive) setManaged(items) })
      .catch((reason: unknown) => { if (alive) setError(reason instanceof Error ? reason.message : String(reason)) })
    return () => { alive = false }
  }, [listManagedResources, resourceKind])

  const normalizedQuery = query.trim().toLocaleLowerCase('zh-CN')
  const builtInOptions = copy.options.filter((option) => `${option.label} ${option.detail}`.toLocaleLowerCase('zh-CN').includes(normalizedQuery))
  const managedOptions = managed.filter((resource) => `${resource.name} ${resource.description} ${resource.sourceLabel}`.toLocaleLowerCase('zh-CN').includes(normalizedQuery))

  function replaceManaged(next: ManagedResource): void {
    setManaged((current) => current.map((item) => item.id === next.id ? next : item))
  }

  async function addLocal(sourceKind: 'file' | 'folder'): Promise<void> {
    setBusy(true)
    setError(null)
    try {
      const added = await selectManagedResources(resourceKind, sourceKind)
      setManaged((current) => [...current, ...added.filter((next) => !current.some((item) => item.id === next.id))])
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  async function addGit(): Promise<void> {
    if (gitUrl.trim() === '') return
    setBusy(true)
    setError(null)
    try {
      const added = await addManagedResourceUrl(resourceKind, gitUrl)
      setManaged((current) => [...current, added])
      setGitUrl('')
      setShowGitForm(false)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy(false)
    }
  }

  async function updateResource(id: string, patch: ManagedResourcePatch): Promise<void> {
    setError(null)
    try {
      replaceManaged(await updateManagedResource(id, patch))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  async function deleteResource(resource: ManagedResource): Promise<void> {
    setError(null)
    try {
      if (await removeManagedResource(resource.id)) {
        setManaged((current) => current.filter((item) => item.id !== resource.id))
        if (task !== undefined && selected.includes(resource.id)) {
          onContextChange({ ...task.context, [copy.key]: selected.filter((id) => id !== resource.id) })
        }
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  return (
    <main className="page resource-page">
      <PageHeader
        title={copy.title}
        description={copy.description}
      />
      <section className="resource-add-row" aria-label={`添加${copy.title}`}>
        <span className="resource-icon"><Icon name={copy.icon} size={21} /></span>
        <div><strong>{kind === 'skills' ? '添加自定义 Skill' : kind === 'knowledge' ? '添加知识源' : '添加第三方应用资源'}</strong><small>从本机文件、文件夹或 Git URL 添加</small></div>
          <details className="resource-add-menu">
            <summary className="round-add-button" aria-label={`添加${copy.title}`} title={`添加${copy.title}`}><Icon name="plus" size={21} /></summary>
            <div>
              <button type="button" disabled={busy} onClick={() => void addLocal('file')}><Icon name="file" size={17} />上传文件</button>
              <button type="button" disabled={busy} onClick={() => void addLocal('folder')}><Icon name="folder" size={17} />上传文件夹</button>
              <button type="button" disabled={busy} onClick={() => setShowGitForm((current) => !current)}><Icon name="graph" size={17} />添加 Git URL</button>
            </div>
          </details>
      </section>
      {showGitForm ? (
        <form className="resource-git-form" onSubmit={(event) => { event.preventDefault(); void addGit() }}>
          <label><span>Git URL</span><input type="url" required value={gitUrl} onChange={(event) => setGitUrl(event.target.value)} placeholder="https://example.com/repository.git" /></label>
          <button type="submit" className="secondary-button" disabled={busy || gitUrl.trim() === ''}>登记来源</button>
        </form>
      ) : null}
      <label className="page-search"><Icon name="search" size={18} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={`搜索${copy.title}`} /></label>
      {error !== null ? <div className="resource-error" role="alert"><Icon name="warning" size={17} />{error}</div> : null}
      {task === undefined ? <div className="plain-empty"><strong>正在准备任务</strong></div> : (
        <>
          <section className="resource-section" aria-labelledby="resource-built-in-heading">
            <header><h2 id="resource-built-in-heading">系统清单</h2><span>{builtInOptions.length} 项</span></header>
            <div className="resource-list">
          {builtInOptions.map((option) => {
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
          </section>
          <section className="resource-section" aria-labelledby="resource-custom-heading">
            <header><h2 id="resource-custom-heading">自定义</h2><span>{managedOptions.length} 项</span></header>
            {managedOptions.length === 0 ? <div className="resource-empty">尚未添加自定义资源</div> : (
              <div className="managed-resource-list">
                {managedOptions.map((resource) => {
                  const checked = selected.includes(resource.id)
                  const unavailable = !resource.enabled || resource.runtimeStatus === 'missing_source'
                  return (
                    <article key={resource.id} className={checked ? 'selected' : ''}>
                      <label className="managed-resource-select">
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={unavailable}
                          onChange={() => onContextChange({ ...task.context, [copy.key]: toggleSelection(selected, resource.id) })}
                        />
                        <span className="resource-icon"><Icon name={copy.icon} size={19} /></span>
                        <span><strong>{resource.name}</strong><small>{resource.description}</small><small>{resource.sourceLabel}</small></span>
                      </label>
                      <div className="managed-resource-controls">
                        <select
                          aria-label={`${resource.name} 作用域`}
                          value={resource.scope}
                          onChange={(event) => void updateResource(resource.id, { scope: event.target.value as ManagedResource['scope'], ...(event.target.value === 'role' ? { roleId: resource.roleId ?? 'coder' } : {}) })}
                        >
                          <option value="global">全局</option><option value="role">角色</option><option value="project">项目</option>
                        </select>
                        {resource.scope === 'role' ? (
                          <select aria-label={`${resource.name} 角色`} value={resource.roleId ?? 'coder'} onChange={(event) => void updateResource(resource.id, { roleId: event.target.value })}>
                            {RESOURCE_ROLES.map(([id, label]) => <option value={id} key={id}>{label}</option>)}
                          </select>
                        ) : null}
                        <button type="button" className="resource-state-button" onClick={() => void updateResource(resource.id, { enabled: !resource.enabled })}>{resource.enabled ? '已启用' : '已停用'}</button>
                        <span className={`resource-runtime ${resource.runtimeStatus}`}>{resource.runtimeStatus === 'missing_source' ? '来源失效' : '待 A/B 运行时接入'}</span>
                        <details className="row-action-menu"><summary className="icon-button" title="管理资源" aria-label={`管理 ${resource.name}`}><Icon name="menu" size={17} /></summary><div><button type="button" onClick={() => void deleteResource(resource)}><Icon name="close" size={16} />删除资源</button></div></details>
                      </div>
                    </article>
                  )
                })}
              </div>
            )}
          </section>
          {kind === 'knowledge' ? <KnowledgeRuntimeStatus snapshot={snapshot} /> : null}
        </>
      )}
    </main>
  )
}

function KnowledgeRuntimeStatus({ snapshot }: { readonly snapshot: StateSnapshot | null }): ReactNode {
  const knowledge = snapshot?.knowledge
  const knowledgeEdges = knowledge?.knowledge ?? []
  const provenanceEdges = knowledge?.provenance ?? []
  return (
    <section className="resource-section knowledge-runtime" aria-labelledby="knowledge-runtime-heading">
      <header>
        <div><h2 id="knowledge-runtime-heading">RAG 与知识投影</h2><p>开发 Agent 必须通过 MemoryIndex 检索后获取知识，不能把登记文件全文直接塞入 Prompt。</p></div>
        <span className="runtime-pending">RAG 未连接</span>
      </header>
      <div className="runtime-contract-grid">
        <div><strong>{knowledgeEdges.length}</strong><span>知识关系</span></div>
        <div><strong>{provenanceEdges.length}</strong><span>溯源关系</span></div>
        <div><strong>未提供</strong><span>索引版本</span></div>
      </div>
      <p className="runtime-boundary"><Icon name="warning" size={17} />当前 A/B 状态源没有提供 MemoryIndex 查询结果、索引版本或引用片段；以下只展示项目已经写入的知识图，不代表 RAG 已完成召回。</p>
      {knowledgeEdges.length === 0 && provenanceEdges.length === 0 ? (
        <div className="resource-empty">当前项目尚未写入知识或溯源关系。</div>
      ) : (
        <div className="knowledge-edge-list">
          {knowledgeEdges.map((edge, index) => (
            <article key={`knowledge-${edge.from}-${edge.to}-${index}`}>
              <code>{edge.from}</code><strong>{edge.relation}</strong><code>{edge.to}</code><span>{Math.round(edge.confidence * 100)}%</span>
            </article>
          ))}
          {provenanceEdges.map((edge, index) => (
            <article key={`provenance-${edge.from}-${edge.to}-${index}`}>
              <code>{edge.from}</code><strong>{edge.relation}</strong><code>{edge.to}</code><span>{new Date(edge.at).toLocaleString('zh-CN')}</span>
            </article>
          ))}
        </div>
      )}
    </section>
  )
}

export function SettingsView({ preferences, onChange, onOpenMcp }: {
  readonly preferences: WorkbenchPreferences
  readonly onChange: (preferences: WorkbenchPreferences) => void
  readonly onOpenMcp: () => void
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
      <section className="settings-section">
        <header><strong>Agent 工具</strong><span>面向开发者和管理员的高级配置，不属于第三方应用插件。</span></header>
        <button type="button" className="settings-link-row" onClick={onOpenMcp}>
          <span className="settings-link-icon"><Icon name="server" size={20} /></span>
          <span><strong>Agent 工具与 MCP</strong><small>配置 MCP Server，查看 A/B 运行时投影的连接状态和可用工具。</small></span>
          <Icon name="chevron" size={18} />
        </button>
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
