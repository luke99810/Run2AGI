import { useEffect, useState, type ReactNode } from 'react'
import type { RoleId, RoleSpec } from '@codentum/contracts'
import type { AgentConfiguration, AgentConfigurationPatch, StateSnapshot } from '../shared/protocol'
import { ROLE_ROSTER, roleLabel, type RoleRosterEntry } from '../renderer/src/domain'
import { EmptyState, Icon, PageHeader } from '../panels/Common'

interface AgentEditor {
  readonly id: string
  readonly name: string
  readonly custom: boolean
  readonly label: string
}

function configurationSummary(config: AgentConfiguration | undefined): string {
  if (config === undefined) return '使用项目默认配置'
  return `${config.systemPrompt === '' ? '默认提示词' : '自定义提示词'} · ${config.systemDocumentName ?? '无系统文档'} · ${config.apiKeyConfigured ? 'API Key 已配置' : '使用网关凭据'}`
}

function SystemRoleCard({ entry, role, config, currentWorkers, taskCount, onConfigure }: {
  readonly entry: RoleRosterEntry
  readonly role: RoleSpec | undefined
  readonly config: AgentConfiguration | undefined
  readonly currentWorkers: number
  readonly taskCount: number
  readonly onConfigure: () => void
}): ReactNode {
  return (
    <article className={`role-card system-role-card${role === undefined ? ' unconfigured' : ''}`}>
      <header>
        <span className="role-avatar">{roleLabel(entry.id).slice(0, 1)}</span>
        <div><h2>{roleLabel(entry.id)}</h2><span>{entry.id} · RoleSpec 模板</span></div>
        <button type="button" className="icon-button role-config-button" title="配置 Agent" onClick={onConfigure}><Icon name="settings" size={18} /></button>
      </header>
      <p>{role?.summary ?? entry.summary}</p>
      <div className="role-activity"><span><strong>{currentWorkers}</strong> 当前 Worker</span><span><strong>{taskCount}</strong> 个任务</span></div>
      <dl>
        <div><dt>运行</dt><dd>{role === undefined ? '等待项目 RoleSpec 投影' : role.usesModel ? '模型 Agent' : '确定性执行'}</dd></div>
        <div><dt>配置</dt><dd>{configurationSummary(config)}</dd></div>
        {role === undefined ? null : <><div><dt>工具</dt><dd>{role.tools.join('、') || '未配置'}</dd></div><div><dt>Skills</dt><dd>{role.skills?.map((skill) => skill.id).join('、') || '未配置'}</dd></div></>}
      </dl>
    </article>
  )
}

function CustomAgentCard({ config, onConfigure, onDelete }: {
  readonly config: AgentConfiguration
  readonly onConfigure: () => void
  readonly onDelete: () => void
}): ReactNode {
  const name = config.name ?? '自定义 Agent'
  return (
    <article className="role-card custom-agent-card">
      <header>
        <span className="role-avatar custom">{name.slice(0, 1)}</span>
        <div><h2>{name}</h2><span>本地自定义配置</span></div>
        <details className="row-action-menu">
          <summary className="icon-button" aria-label={`管理 ${name}`} title="管理配置"><Icon name="menu" size={17} /></summary>
          <div>
            <button type="button" onClick={onConfigure}><Icon name="settings" size={16} />编辑配置</button>
            <button type="button" onClick={onDelete}><Icon name="close" size={16} />删除配置</button>
          </div>
        </details>
      </header>
      <p>{config.systemPrompt || '尚未填写系统提示词。'}</p>
      <div className="role-activity"><span><strong>0</strong> 当前 Worker</span><span><strong>0</strong> 个任务</span></div>
      <dl>
        <div><dt>运行</dt><dd className="runtime-not-connected">A/B 未接入，不可运行</dd></div>
        <div><dt>配置</dt><dd>{configurationSummary(config)}</dd></div>
      </dl>
    </article>
  )
}

export function RolesView({ snapshot, listConfigurations, saveConfiguration, removeConfiguration, selectSystemDocument, clearSystemDocument }: {
  readonly snapshot: StateSnapshot | null
  readonly listConfigurations: () => Promise<readonly AgentConfiguration[]>
  readonly saveConfiguration: (roleId: string, patch: AgentConfigurationPatch) => Promise<AgentConfiguration>
  readonly removeConfiguration: (roleId: string) => Promise<boolean>
  readonly selectSystemDocument: (roleId: string) => Promise<AgentConfiguration>
  readonly clearSystemDocument: (roleId: string) => Promise<AgentConfiguration>
}): ReactNode {
  const roles = snapshot?.roles ?? []
  const roleById = new Map<RoleId, RoleSpec>(roles.map((role) => [role.id, role]))
  const [configs, setConfigs] = useState<readonly AgentConfiguration[]>([])
  const [editing, setEditing] = useState<AgentEditor | null>(null)
  const [deleting, setDeleting] = useState<AgentConfiguration | null>(null)
  const [name, setName] = useState('')
  const [prompt, setPrompt] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [clearApiKey, setClearApiKey] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => { void listConfigurations().then(setConfigs).catch((reason: unknown) => setError(String(reason))) }, [listConfigurations])
  const customConfigs = configs.filter((item) => item.custom === true || item.roleId.startsWith('custom:'))
  const selectedConfig = editing === null ? undefined : configs.find((item) => item.roleId === editing.id)
  function replace(next: AgentConfiguration): void { setConfigs((current) => [...current.filter((item) => item.roleId !== next.roleId), next]) }
  function open(editor: AgentEditor): void {
    const config = configs.find((item) => item.roleId === editor.id)
    setEditing(editor)
    setName(config?.name ?? editor.name)
    setPrompt(config?.systemPrompt ?? '')
    setApiKey('')
    setClearApiKey(false)
    setError(null)
  }
  function openSystem(entry: RoleRosterEntry): void {
    open({ id: entry.id, name: roleLabel(entry.id), custom: false, label: roleLabel(entry.id) })
  }
  function createCustom(): void {
    const id = `custom:${globalThis.crypto.randomUUID()}`
    open({ id, name: '', custom: true, label: '新建本地 Agent' })
  }
  async function saveEditing(): Promise<void> {
    if (editing === null) return
    try {
      const next = await saveConfiguration(editing.id, {
        ...(editing.custom ? { name } : {}),
        systemPrompt: prompt,
        ...(apiKey === '' ? {} : { apiKey }),
        ...(clearApiKey ? { clearApiKey: true } : {})
      })
      replace(next)
      setEditing(null)
      setError(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }
  async function chooseSystemDocument(): Promise<void> {
    if (editing === null) return
    try {
      if (editing.custom && selectedConfig === undefined) {
        if (name.trim() === '') throw new Error('请先填写 Agent 名称。')
        replace(await saveConfiguration(editing.id, { name, systemPrompt: prompt }))
      }
      replace(await selectSystemDocument(editing.id))
      setError(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }
  async function confirmDelete(): Promise<void> {
    if (deleting === null) return
    try {
      await removeConfiguration(deleting.roleId)
      setConfigs((current) => current.filter((item) => item.roleId !== deleting.roleId))
      setDeleting(null)
      setError(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  return (
    <div className="page roles-view">
      <PageHeader
        eyebrow={`RoleSpec 模板 ${ROLE_ROSTER.length} · 项目投影 ${roles.length} · 真实 Worker ${snapshot?.workers.length ?? 0}`}
        title="研发团队"
        description="系统岗位来自 A/B 的 RoleSpec；只有 B 投影出的 Worker 才是真实运行 Agent。本地自定义配置可增删，但在 A/B 消费配置前不会被标成可运行。"
      />
      <section className="team-agent-add-row">
        <span className="connector-logo"><Icon name="people" size={21} /></span>
        <div><strong>本地自定义 Agent 配置</strong><small>配置系统提示词、Markdown 系统文档和独立 API Key；硬权限仍由 RoleSpec 与 Guardian 决定。</small></div>
        <details className="round-action-menu">
          <summary className="round-add-button" aria-label="新建 Agent 配置" title="新建 Agent 配置"><Icon name="plus" size={21} /></summary>
          <div><button type="button" onClick={(event) => { const menu = event.currentTarget.closest('details'); if (menu instanceof HTMLDetailsElement) menu.open = false; createCustom() }}><Icon name="plus" size={16} />新建本地 Agent 配置</button></div>
        </details>
      </section>
      {error === null ? null : <div className="resource-error" role="alert"><Icon name="warning" size={17} />{error}</div>}
      <section className="custom-agent-section" aria-labelledby="custom-agent-heading">
        <header><div><span>本地配置</span><h2 id="custom-agent-heading">自定义 Agent</h2></div><small>{customConfigs.length} 项 · 当前不代表运行实例</small></header>
        {customConfigs.length === 0
          ? <EmptyState icon="people" title="尚未添加自定义 Agent" detail="点击上方加号新建配置。真实运行仍需 A/B 创建 RoleSpec、WorkPacket 和 Worker。" />
          : <div className="role-grid custom-agent-grid">{customConfigs.map((config) => <CustomAgentCard key={config.roleId} config={config} onConfigure={() => open({ id: config.roleId, name: config.name ?? '自定义 Agent', custom: true, label: config.name ?? '自定义 Agent' })} onDelete={() => setDeleting(config)} />)}</div>}
      </section>
      <section className="system-role-section" aria-labelledby="system-role-heading">
        <header><div><span>A/B 契约</span><h2 id="system-role-heading">系统 RoleSpec 模板</h2></div><small>系统岗位不可在 C 侧删除</small></header>
        <div className="role-grid">
          {ROLE_ROSTER.map((entry) => <SystemRoleCard key={entry.id} entry={entry} role={roleById.get(entry.id)} config={configs.find((item) => item.roleId === entry.id)} currentWorkers={snapshot?.workers.filter((worker) => worker.role === entry.id && ['starting', 'running', 'waiting'].includes(worker.state)).length ?? 0} taskCount={snapshot?.packets.filter((packet) => packet.role === entry.id).length ?? 0} onConfigure={() => openSystem(entry)} />)}
        </div>
      </section>
      {editing === null ? null : (
        <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setEditing(null) }}>
          <section className="agent-config-dialog" role="dialog" aria-modal="true" aria-labelledby="agent-config-title">
            <header><div><span>{editing.custom ? '本地自定义配置' : `${editing.id} · 系统 RoleSpec`}</span><h2 id="agent-config-title">{editing.custom ? editing.label : `配置${editing.label}`}</h2></div><button className="icon-button" type="button" onClick={() => setEditing(null)} aria-label="关闭"><Icon name="close" size={19} /></button></header>
            {editing.custom ? <label><span>Agent 名称</span><input required value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：前端开发 Agent" /></label> : null}
            <label><span>系统提示词</span><textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="补充该 Agent 的工作方式。硬约束请写入 RoleSpec，不要只依赖提示词。" /></label>
            <div className="agent-document-row"><div><strong>系统文档</strong><span>{selectedConfig?.systemDocumentName ?? '未添加，仅支持 Markdown'}</span></div><button className="secondary-button compact-button" type="button" onClick={() => void chooseSystemDocument()}>添加 .md</button>{selectedConfig?.systemDocumentName === undefined ? null : <button className="secondary-button compact-button" type="button" onClick={() => void clearSystemDocument(editing.id).then(replace).catch((reason: unknown) => setError(String(reason)))}>移除</button>}</div>
            <label><span>独立 API Key</span><input type="password" value={apiKey} disabled={clearApiKey} onChange={(event) => setApiKey(event.target.value)} placeholder={selectedConfig?.apiKeyConfigured === true ? '已安全保存，留空保持不变' : '留空则使用 ModelGateway 配置'} /></label>
            <label className="inline-check"><input type="checkbox" checked={clearApiKey} disabled={selectedConfig?.apiKeyConfigured !== true} onChange={(event) => { setClearApiKey(event.target.checked); if (event.target.checked) setApiKey('') }} />清除已保存 API Key</label>
            <p className="dialog-note">配置保存于本机安全存储。B 的 Harness 尚未消费 AgentConfiguration，因此本地自定义 Agent 当前不可运行；页面不会伪造已连接状态。</p>
            <footer><button type="button" className="secondary-button" onClick={() => setEditing(null)}>取消</button><button type="button" className="primary-button" disabled={editing.custom && name.trim() === ''} onClick={() => void saveEditing()}>保存 Agent 配置</button></footer>
          </section>
        </div>
      )}
      {deleting === null ? null : (
        <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setDeleting(null) }}>
          <section className="agent-delete-dialog" role="alertdialog" aria-modal="true" aria-labelledby="agent-delete-title">
            <span className="warning-icon"><Icon name="warning" size={20} /></span>
            <div><h2 id="agent-delete-title">删除“{deleting.name ?? '自定义 Agent'}”配置？</h2><p>这只删除本地配置和已保存凭据，不会删除项目文件或 A/B 的运行记录。</p></div>
            <footer><button type="button" className="secondary-button" onClick={() => setDeleting(null)}>取消</button><button type="button" className="danger-button" onClick={() => void confirmDelete()}>删除配置</button></footer>
          </section>
        </div>
      )}
    </div>
  )
}
