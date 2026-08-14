import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import type { ConnectorConfiguration, ConnectorConfigurationInput, McpServiceProjection } from '../shared/protocol'
import { EmptyState, Icon, PageHeader } from '../panels/Common'

const EMPTY: ConnectorConfigurationInput = { provider: 'custom', name: '', accountLabel: '', enabled: true }

export function ConnectorsView({ services, list, save, remove }: {
  readonly services: readonly McpServiceProjection[]
  readonly list: () => Promise<readonly ConnectorConfiguration[]>
  readonly save: (input: ConnectorConfigurationInput) => Promise<ConnectorConfiguration>
  readonly remove: (id: string) => Promise<boolean>
}): ReactNode {
  const [items, setItems] = useState<readonly ConnectorConfiguration[]>([])
  const [editing, setEditing] = useState<ConnectorConfigurationInput | null>(null)
  const [error, setError] = useState<string | null>(null)
  const applications = services.filter((service) => service.category === 'third-party-app')

  useEffect(() => { void list().then(setItems).catch((reason: unknown) => setError(String(reason))) }, [list])

  async function submit(event: FormEvent): Promise<void> {
    event.preventDefault()
    if (editing === null) return
    try {
      const saved = await save(editing)
      setItems((current) => [...current.filter((item) => item.id !== saved.id), saved])
      setEditing(null)
      setError(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }

  return (
    <main className="page connector-page">
      <PageHeader eyebrow="第三方应用" title="连接器" description="读取 B 投影的第三方应用用途、配置要求和真实连接状态；本地凭据配置与 MCP 技术服务分开管理。" />
      <section className="connector-config-section application-catalog" aria-labelledby="application-catalog-heading">
        <header><h2 id="application-catalog-heading">第三方应用目录</h2><span>{applications.length} 项</span></header>
        {applications.length === 0 ? (
          <EmptyState icon="plug" title="没有第三方应用投影" detail="等待 A/B 将 category=third-party-app 的配置投影到当前项目。" />
        ) : (
          <div className="application-list">{applications.map((service) => {
            const docs = service.docs?.startsWith('https://') === true ? service.docs : undefined
            return <article className="application-item" key={service.id}>
              <span className="connector-logo"><Icon name="plug" size={20} /></span>
              <div className="application-copy">
                <strong>{service.name}</strong>
                <small>{service.purpose ?? '未提供用途说明'}</small>
                <div className="application-meta">
                  <span>{service.transport}</span>
                  <span>{service.enabled ? '配置已启用' : '配置未启用'}</span>
                  <span>{service.requiresEnv?.length ? `需要 ${service.requiresEnv.join('、')}` : '无需环境凭据'}</span>
                  {docs === undefined ? null : <a href={docs} target="_blank" rel="noreferrer">配置文档</a>}
                </div>
                {service.credentialHowTo === undefined ? null : <small>凭据：{service.credentialHowTo}</small>}
                {service.error === undefined ? null : <small className="application-error">{service.error}</small>}
              </div>
              <div className="connector-state" aria-label={`${service.name} 连接状态`}>
                <span className={`config-state status-${service.status}`}>{service.status === 'connected' ? '已连接' : service.status === 'connecting' ? '连接中' : service.status === 'error' ? '错误' : '未连接'}</span>
                <small>{service.authentication === 'configured' ? '凭据已配置' : service.authentication === 'not_required' ? '无需凭据' : service.authentication === 'missing' ? '缺少凭据' : '鉴权未知'}</small>
              </div>
            </article>
          })}</div>
        )}
      </section>
      <section className="connector-add-row">
        <span className="connector-logo"><Icon name="plug" size={21} /></span>
        <div><strong>新增连接器配置</strong><small>凭据使用系统安全存储加密；真实连接状态等待后续运行时接口。</small></div>
        <details className="round-action-menu">
          <summary className="round-add-button" aria-label="新增连接器配置" title="新增连接器配置"><Icon name="plus" size={21} /></summary>
          <div><button type="button" onClick={() => setEditing(EMPTY)}><Icon name="plus" size={16} />新建本地配置</button></div>
        </details>
      </section>
      {error === null ? null : <div className="resource-error" role="alert"><Icon name="warning" size={17} />{error}</div>}
      {editing === null ? null : (
        <form className="configuration-form" onSubmit={(event) => void submit(event)}>
          <div className="form-heading"><div><strong>{editing.id === undefined ? '新建本地配置' : '编辑本地配置'}</strong><span>这里只保存配置，不执行 OAuth 或 API 连通性探测。</span></div><button className="icon-button" type="button" onClick={() => setEditing(null)} aria-label="关闭"><Icon name="close" size={18} /></button></div>
          <label><span>配置名称</span><input required value={editing.name} onChange={(event) => setEditing({ ...editing, name: event.target.value })} placeholder="例如：研发协作账号" /></label>
          <label><span>账号或工作区</span><input value={editing.accountLabel} onChange={(event) => setEditing({ ...editing, accountLabel: event.target.value })} placeholder="例如：研发团队" /></label>
          <label><span>访问凭据</span><input type="password" value={editing.credential ?? ''} onChange={(event) => setEditing({ ...editing, credential: event.target.value })} placeholder={editing.id === undefined ? 'Token / Secret' : '留空则保留原凭据'} /></label>
          <label className="inline-check"><input type="checkbox" checked={editing.enabled} onChange={(event) => setEditing({ ...editing, enabled: event.target.checked })} />运行时接入后允许使用（不代表当前已连接）</label>
          <div className="form-actions"><button type="button" className="secondary-button" onClick={() => setEditing(null)}>取消</button><button type="submit" className="primary-button">保存配置</button></div>
        </form>
      )}
      <section className="connector-config-section" aria-labelledby="connector-config-heading">
        <header><h2 id="connector-config-heading">本地配置</h2><span>{items.length} 项</span></header>
        {items.length === 0 ? <EmptyState icon="plug" title="尚未添加连接器配置" detail="点击上方加号登记配置；当前不会连接任何第三方应用。" /> : <div className="connector-configurations">{items.map((item) => <article className="connector-item" key={item.id}>
          <div><strong>{item.name}</strong><small>{item.accountLabel || '未填写账号或工作区'} · {item.credentialConfigured ? '凭据已安全保存' : '未配置凭据'}</small></div>
          <div className="connector-state" aria-label={`${item.name} 连接状态`}><span className="config-state">{item.enabled ? '本地已登记' : '本地已停用'}</span><small>运行时未接入</small></div>
          <details className="row-action-menu"><summary className="icon-button" aria-label={`管理 ${item.name}`} title="管理配置"><Icon name="menu" size={17} /></summary><div><button type="button" onClick={() => setEditing({ ...item })}><Icon name="settings" size={16} />编辑配置</button><button type="button" onClick={() => void remove(item.id).then(() => setItems((current) => current.filter((entry) => entry.id !== item.id)))}><Icon name="close" size={16} />删除配置</button></div></details>
        </article>)}</div>}
      </section>
      <p className="integration-boundary"><Icon name="warning" size={16} />任务书当前只要求 C 提供连接器管理界面、凭据安全保存和真实状态展示。由于没有第三方连接器运行时，本页只显示本地登记状态，不显示“已连接”。</p>
    </main>
  )
}
