import { useEffect, useState, type ReactNode } from 'react'
import type { RoleId, RoleSpec } from '@codentum/contracts'
import type { AgentConfiguration, AgentConfigurationPatch, AgentRuntimeProfile, ModelEffort, StateSnapshot } from '../shared/protocol'
import { GLOBAL_SCOPE, MODEL_EFFORTS, ORCHESTRATOR_SCOPE } from '../shared/protocol'
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

function modelSummary(role: RoleSpec): string {
  if (!role.usesModel) return '确定性执行，不调用模型'
  const model = role.modelPolicy?.defaultModel ?? '由 ModelGateway 选择'
  return role.modelPolicy?.defaultEffort === undefined
    ? model
    : `${model} · ${role.modelPolicy.defaultEffort}`
}

function escalationSummary(role: RoleSpec): string {
  const policy = role.escalation
  if (policy === undefined) return '未配置'
  return [
    policy.maxSelfRepair === undefined ? undefined : `自修复 ${policy.maxSelfRepair} 次`,
    policy.peerDebugEnabled ? '允许 Peer-Debug' : undefined,
    policy.escalateTo === undefined ? undefined : `升级至 ${policy.escalateTo}`
  ].filter((part): part is string => part !== undefined).join(' · ') || '未配置'
}


/**
 * 某个 Agent 的运行画像：**现在用什么在跑、跑得怎么样**。
 *
 * ★ 数据来自引擎写的 `.codentum/agents.json`，不是界面自己算的。
 *   界面算会得到一个「看起来更实时」但没有权威来源的数字。
 *
 * ★ 没有画像时说「引擎尚未产出」而不是显示 0 ——
 *   「跑过 0 次」与「引擎没在写这份投影」是完全不同的两件事，
 *   显示成同一个 0 会把后者伪装成前者。
 */
function AgentRuntimeRow({ profile }: { readonly profile: AgentRuntimeProfile | undefined }): ReactNode {
  if (profile === undefined) {
    return <p className="agent-runtime empty">引擎尚未产出该 Agent 的画像（不等于它跑过 0 次）</p>
  }
  const model = profile.config?.model
  return (
    <div className="agent-runtime">
      <span className="agent-runtime-model" title={`模型来自${layerLabel(profile.config?.source?.['model'])}`}>
        <code>{model ?? '—'}</code>
        <small>{profile.config?.effort ?? '—'}</small>
      </span>
      <span><strong>{profile.packets.total}</strong> packet</span>
      <span><strong>{profile.attempts}</strong> 次尝试</span>
      <span><strong>{profile.spentCny.toFixed(2)}</strong> 元</span>
      {profile.lastActivityAt == null ? null : <small className="agent-runtime-time">最近 {profile.lastActivityAt.slice(5, 16).replace('T', ' ')}</small>}
    </div>
  )
}

/** 全局 / 主 Agent 这两个作用域的卡片。 */
function ScopeCard({ title, detail, config, profile, onConfigure }: {
  readonly title: string
  readonly detail: string
  readonly config: AgentConfiguration | undefined
  readonly profile: AgentRuntimeProfile | undefined
  readonly onConfigure: () => void
}): ReactNode {
  const endpoint = config?.endpoint
  return (
    <article className="role-card scope-card">
      <header>
        <span className="role-avatar"><Icon name="settings" size={18} /></span>
        <div><h2>{title}</h2><span>模型接入作用域</span></div>
        <button type="button" className="icon-button role-config-button" title="配置" onClick={onConfigure}><Icon name="settings" size={18} /></button>
      </header>
      <p>{detail}</p>
      <dl>
        <div><dt>模型</dt><dd>{endpoint?.model ?? '未配置（回落）'}</dd></div>
        <div><dt>强度</dt><dd>{endpoint?.effort ?? '未配置（回落）'}</dd></div>
        <div><dt>API Key</dt><dd>{config?.apiKeyConfigured === true ? '已保存' : '未配置'}</dd></div>
      </dl>
      {profile === undefined ? null : <AgentRuntimeRow profile={profile} />}
    </article>
  )
}

function SystemRoleCard({ entry, role, config, currentWorkers, taskCount, onConfigure, profile }: {
  readonly entry: RoleRosterEntry
  readonly role: RoleSpec | undefined
  readonly config: AgentConfiguration | undefined
  readonly currentWorkers: number
  readonly taskCount: number
  readonly onConfigure: () => void
  readonly profile: AgentRuntimeProfile | undefined
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
      <AgentRuntimeRow profile={profile} />
      <dl>
        <div><dt>运行</dt><dd>{role === undefined ? '等待项目 RoleSpec 投影' : role.usesModel ? '模型 Agent' : '确定性执行'}</dd></div>
        <div><dt>配置</dt><dd>{configurationSummary(config)}</dd></div>
        {role === undefined ? null : <>
          <div><dt>Prompt</dt><dd>{role.promptRef ?? '未配置'}</dd></div>
          <div><dt>模型</dt><dd>{modelSummary(role)}</dd></div>
          <div><dt>工具</dt><dd>{role.tools.join('、') || '未配置'}</dd></div>
          <div><dt>Skills</dt><dd>{role.skills?.map((skill) => `${skill.id}（${skill.state ?? 'active'} / ${skill.scope}）`).join('、') || '未配置'}</dd></div>
        </>}
      </dl>
      {role === undefined ? null : (
        <details className="role-contract-details">
          <summary>查看 B RoleSpec 完整边界</summary>
          <dl>
            <div><dt>可写</dt><dd>{role.writes.join('、') || '无'}</dd></div>
            <div><dt>只读</dt><dd>{role.reads.join('、') || '无'}</dd></div>
            <div><dt>不可见</dt><dd>{role.invisible?.join('、') || '无'}</dd></div>
            <div><dt>升级</dt><dd>{escalationSummary(role)}</dd></div>
            <div><dt>转换</dt><dd>{role.transitions.map((transition) => `${transition.from} → ${transition.to}${transition.requiresGate === undefined ? '' : ` [${transition.requiresGate}]`}`).join('；') || '无'}</dd></div>
          </dl>
        </details>
      )}
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


/** 生效配置里某个字段的来源层，翻成使用者看得懂的话。 */
function layerLabel(layer?: string): string {
  switch (layer) {
    case 'agent': return '该 Agent'
    case 'orchestrator': return '主 Agent'
    case 'roleSpec': return '角色定义'
    case 'global': return '全局'
    case 'fallback': return '启动默认'
    default: return '未知'
  }
}

/**
 * 输入框的占位提示：显示**不填的话会用什么**。
 *
 * ★ 空输入框配一句「留空则使用默认」等于没说 —— 使用者仍然不知道那个
 *   默认是什么。直接把生效值放进占位符，「要不要覆盖」才是个能判断的问题。
 */
function placeholderFor(effective: AgentRuntimeProfile['config'], field: 'model' | 'baseUrl'): string {
  const value = effective?.[field]
  return typeof value === 'string' && value !== '' ? `跟随上一层：${value}` : '跟随上一层'
}

/** 这个 Agent 的运行画像（来自引擎写的 agents.json）。 */
function profileOf(snapshot: StateSnapshot | null, roleId: string): AgentRuntimeProfile | undefined {
  const role = roleId === ORCHESTRATOR_SCOPE ? 'planner' : roleId
  return snapshot?.agents.find((item) => item.role === role)
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
  const [model, setModel] = useState('')
  const [effort, setEffort] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => { void listConfigurations().then(setConfigs).catch((reason: unknown) => setError(String(reason))) }, [listConfigurations])
  const customConfigs = configs.filter((item) => item.custom === true || item.roleId.startsWith('custom:'))
  const selectedConfig = editing === null ? undefined : configs.find((item) => item.roleId === editing.id)
  const effective = editing === null ? undefined : profileOf(snapshot, editing.id)?.config
  function replace(next: AgentConfiguration): void { setConfigs((current) => [...current.filter((item) => item.roleId !== next.roleId), next]) }
  function open(editor: AgentEditor): void {
    const config = configs.find((item) => item.roleId === editor.id)
    setEditing(editor)
    setName(config?.name ?? editor.name)
    setPrompt(config?.systemPrompt ?? '')
    setApiKey('')
    setClearApiKey(false)
    setModel(config?.endpoint?.model ?? '')
    setEffort(config?.endpoint?.effort ?? '')
    setBaseUrl(config?.endpoint?.baseUrl ?? '')
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
        ...(clearApiKey ? { clearApiKey: true } : {}),
        // ★ 三个字段一律传（含空串）：空串是「取消这一层的配置」，
        //   与「没动这个字段」必须可区分 —— 否则使用者会发现清不掉。
        // ★ 三个字段一律传（含空串）—— 空串是「取消这一层」，
        //   省略字段才是「不改动」。曾经为了迁就类型把空 effort 写成省略，
        //   于是清除变成了静默无操作。
        endpoint: { model, baseUrl, effort: effort as ModelEffort | '' }
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
        description="系统岗位、Prompt 引用、模型策略、Skill 绑定和权限边界来自 B 的项目 RoleSpec；只有 B 投影出的 Worker 才是真实运行 Agent。"
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
      {/* ★ 全局与主 Agent 是**作用域**，不是角色 —— 但它们和各子 Agent 用
             同一个配置弹窗，因为要配的东西完全一样。放在最前面是因为
             它们是另外两层的默认值：先看默认，再看谁覆盖了它。 */}
      <section className="scope-config-section" aria-labelledby="scope-config-heading">
        <header><div><span>模型接入</span><h2 id="scope-config-heading">全局与主 Agent</h2></div><small>各子 Agent 未单独配置时，按此回落</small></header>
        <div className="role-grid">
          <ScopeCard
            title="全局默认"
            detail="所有 Agent 的兜底。任何一层没配的字段都回落到这里。"
            config={configs.find((item) => item.roleId === GLOBAL_SCOPE)}
            profile={undefined}
            onConfigure={() => open({ id: GLOBAL_SCOPE, name: '全局默认', custom: false, label: '全局默认' })}
          />
          <ScopeCard
            title="主 Agent（规划）"
            detail="把需求拆成任务的那个。只影响它自己，不影响各子 Agent。"
            config={configs.find((item) => item.roleId === ORCHESTRATOR_SCOPE)}
            profile={profileOf(snapshot, ORCHESTRATOR_SCOPE)}
            onConfigure={() => open({ id: ORCHESTRATOR_SCOPE, name: '主 Agent', custom: false, label: '主 Agent' })}
          />
        </div>
      </section>
      <section className="custom-agent-section" aria-labelledby="custom-agent-heading">
        <header><div><span>本地配置</span><h2 id="custom-agent-heading">自定义 Agent</h2></div><small>{customConfigs.length} 项 · 当前不代表运行实例</small></header>
        {customConfigs.length === 0
          ? <EmptyState icon="people" title="尚未添加自定义 Agent" detail="点击上方加号新建配置。真实运行仍需 A/B 创建 RoleSpec、WorkPacket 和 Worker。" />
          : <div className="role-grid custom-agent-grid">{customConfigs.map((config) => <CustomAgentCard key={config.roleId} config={config} onConfigure={() => open({ id: config.roleId, name: config.name ?? '自定义 Agent', custom: true, label: config.name ?? '自定义 Agent' })} onDelete={() => setDeleting(config)} />)}</div>}
      </section>
      <section className="system-role-section" aria-labelledby="system-role-heading">
        <header><div><span>A/B 契约</span><h2 id="system-role-heading">系统 RoleSpec 模板</h2></div><small>系统岗位不可在 C 侧删除</small></header>
        <div className="role-grid">
          {ROLE_ROSTER.map((entry) => <SystemRoleCard key={entry.id} entry={entry} role={roleById.get(entry.id)} config={configs.find((item) => item.roleId === entry.id)} currentWorkers={snapshot?.workers.filter((worker) => worker.role === entry.id && ['starting', 'running', 'waiting'].includes(worker.state)).length ?? 0} taskCount={snapshot?.packets.filter((packet) => packet.role === entry.id).length ?? 0} onConfigure={() => openSystem(entry)} profile={profileOf(snapshot, entry.id)} />)}
        </div>
      </section>
      {editing === null ? null : (
        <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setEditing(null) }}>
          <section className="agent-config-dialog" role="dialog" aria-modal="true" aria-labelledby="agent-config-title">
            <header><div><span>{editing.custom ? '本地自定义配置' : `${editing.id} · 系统 RoleSpec`}</span><h2 id="agent-config-title">{editing.custom ? editing.label : `配置${editing.label}`}</h2></div><button className="icon-button" type="button" onClick={() => setEditing(null)} aria-label="关闭"><Icon name="close" size={19} /></button></header>
            {editing.custom ? <label><span>Agent 名称</span><input required value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：前端开发 Agent" /></label> : null}
            <label><span>追加系统提示词</span><textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="补充该 Agent 的工作方式。硬约束请写入 RoleSpec —— 这里写的内容不授予任何权限。" /></label>
            {/* ★ 说清合并规则。提示词是**叠加**（各层都生效），
                   而模型接入是**覆盖**（高层赢）—— 同一个弹窗里两种规则，
                   不写明白使用者一定会按错的那个去理解。 */}
            <p className="dialog-note prompt-note">追加的提示词会**与上层叠加**（全局 → 主 Agent → 该 Agent 依次拼接，各层都生效），与模型接入「高层覆盖低层」的规则不同。它会带着来源标注写进每次执行的 <code>prompt/system.md</code>，可在证据里核对。</p>
            <div className="agent-document-row"><div><strong>系统文档</strong><span>{selectedConfig?.systemDocumentName ?? '未添加，仅支持 Markdown'}</span></div><button className="secondary-button compact-button" type="button" onClick={() => void chooseSystemDocument()}>添加 .md</button>{selectedConfig?.systemDocumentName === undefined ? null : <button className="secondary-button compact-button" type="button" onClick={() => void clearSystemDocument(editing.id).then(replace).catch((reason: unknown) => setError(String(reason)))}>移除</button>}</div>
            <fieldset className="agent-endpoint">
              <legend>模型接入</legend>
              <label><span>模型</span><input value={model} onChange={(event) => setModel(event.target.value)} placeholder={placeholderFor(effective, 'model')} /></label>
              <label><span>推理强度</span>
                <select value={effort} onChange={(event) => setEffort(event.target.value)}>
                  <option value="">跟随上一层（{effective?.effort ?? '默认'}）</option>
                  {MODEL_EFFORTS.map((item) => <option key={item} value={item}>{item}</option>)}
                </select>
              </label>
              <label><span>接入地址</span><input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder={placeholderFor(effective, 'baseUrl')} /></label>
              <label><span>独立 API Key</span><input type="password" value={apiKey} disabled={clearApiKey} onChange={(event) => setApiKey(event.target.value)} placeholder={selectedConfig?.apiKeyConfigured === true ? '已安全保存，留空保持不变' : '留空则跟随上一层'} /></label>
              <label className="inline-check"><input type="checkbox" checked={clearApiKey} disabled={selectedConfig?.apiKeyConfigured !== true} onChange={(event) => { setClearApiKey(event.target.checked); if (event.target.checked) setApiKey('') }} />清除已保存 API Key</label>
              {effective === undefined ? null : (
                <p className="endpoint-effective">
                  {/* ★ 显示「实际生效的是什么、来自哪一层」。
                        没有这个，「我明明配了却没生效」有三种原因（没保存 /
                        被 RoleSpec 覆盖 / 引擎还没读到），现象完全一样。 */}
                  当前生效：<code>{effective.model ?? '—'}</code> · {effective.effort ?? '—'}
                  <span className="endpoint-source">（模型来自{layerLabel(effective.source?.['model'])}，强度来自{layerLabel(effective.source?.['effort'])}）</span>
                </p>
              )}
              <p className="dialog-note">留空 = 跟随上一层（该 Agent → 主 Agent → RoleSpec → 全局 → 启动默认）。API Key 保存在本机安全存储，只在拉起引擎时作为环境变量注入，不写入任何文件。</p>
            </fieldset>

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
