import type { ReactNode } from 'react'
import type { StateSnapshot } from '../shared/protocol'
import { NAVIGATION, type NavigationKey, packetCounts, roleLabel } from '../renderer/src/domain'
import { Icon } from './Common'

export function Sidebar({ active, snapshot, onNavigate, onSelectWorker, collapsed, onToggle }: {
  readonly active: NavigationKey
  readonly snapshot: StateSnapshot | null
  readonly onNavigate: (next: NavigationKey) => void
  readonly onSelectWorker: (workerId: string) => void
  readonly collapsed: boolean
  readonly onToggle: () => void
}): ReactNode {
  const counts = packetCounts(snapshot?.packets ?? [])
  const currentWorkers = snapshot?.workers.filter((worker) => worker.state === 'running' || worker.state === 'starting' || worker.state === 'waiting') ?? []
  return (
    <aside className={`sidebar${collapsed ? ' collapsed' : ''}`}>
      <div className="brand-row">
        <div className="brand-mark" aria-hidden="true"><span /><span /><span /></div>
        <div className="brand-copy"><strong>Codentum</strong><span>软件研发团队</span></div>
        <button className="icon-button sidebar-toggle" type="button" onClick={onToggle} aria-label={collapsed ? '展开侧栏' : '收起侧栏'}>
          <Icon name="menu" size={20} />
        </button>
      </div>

      <nav className="primary-nav" aria-label="主要功能">
        {NAVIGATION.map((item) => (
          <button
            key={item.id}
            type="button"
            className={active === item.id ? 'active' : ''}
            onClick={() => onNavigate(item.id)}
            title={collapsed ? item.label : undefined}
            aria-current={active === item.id ? 'page' : undefined}
          >
            <Icon name={item.icon as Parameters<typeof Icon>[0]['name']} size={20} />
            <span>{item.label}</span>
            {item.id === 'execution' && currentWorkers.length > 0 ? <em>{currentWorkers.length}</em> : null}
            {item.id === 'board' && counts.blocked > 0 ? <em className="danger-count">{counts.blocked}</em> : null}
          </button>
        ))}
      </nav>

      <section className="agent-rail" aria-label="Worker 状态">
        <div className="rail-heading">
          <span>当前 Worker</span>
          <small>{currentWorkers.length}</small>
        </div>
        {currentWorkers.length === 0 ? (
          <p className="rail-empty">当前没有 Worker</p>
        ) : currentWorkers.slice(0, 5).map((worker) => (
          <button type="button" className="rail-worker" key={worker.workerId} onClick={() => onSelectWorker(worker.workerId)}>
            <span className={`worker-dot worker-${worker.state}`} />
            <span className="rail-worker-copy">
              <strong>{roleLabel(worker.role)}</strong>
              <small>{worker.state === 'starting' ? '正在启动' : worker.state === 'running' ? '执行中' : '等待中'} · {worker.currentModule ?? worker.packetId}</small>
            </span>
            <Icon name="chevron" size={16} />
          </button>
        ))}
        {snapshot !== null && snapshot.workers.length === 0 && snapshot.packets.length > 0 ? (
          <p className="rail-note">项目只有任务状态，尚未收到真实 Worker 投影。</p>
        ) : null}
      </section>

      <div className="sidebar-footer">
        <span className={`connection-dot ${snapshot === null ? 'offline' : 'online'}`} />
        <div><strong>{snapshot?.source.label ?? '未选择项目'}</strong><small>{snapshot?.source.kind === 'fixture' ? '演示快照' : '本地状态'}</small></div>
      </div>
    </aside>
  )
}
