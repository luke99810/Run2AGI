import type { ReactNode } from 'react'
import type { StateSnapshot } from '../shared/protocol'
import { NAVIGATION, type NavigationKey, roleLabel } from '../renderer/src/domain'
import type { TaskSession } from '../renderer/src/task-library'
import brandLogo from '../assets/logo.png'
import { Icon } from './Common'

const PRIMARY_NAV = new Set<NavigationKey>(['conversations', 'plugins', 'knowledge', 'skills', 'cost', 'roles', 'delivery'])
const PROJECT_NAV = new Set<NavigationKey>(['execution', 'waves', 'dependency', 'evidence'])

export function Sidebar({ active, snapshot, tasks, activeTaskId, validationEnabled, onNavigate, onSelectWorker, onNewTask, onSelectTask, collapsed, onToggle }: {
  readonly active: NavigationKey
  readonly snapshot: StateSnapshot | null
  readonly tasks: readonly TaskSession[]
  readonly activeTaskId: string | null
  readonly validationEnabled: boolean
  readonly onNavigate: (next: NavigationKey) => void
  readonly onSelectWorker: (workerId: string) => void
  readonly onNewTask: () => void
  readonly onSelectTask: (taskId: string) => void
  readonly collapsed: boolean
  readonly onToggle: () => void
}): ReactNode {
  const currentWorkers = snapshot?.workers.filter((worker) => worker.state === 'running' || worker.state === 'starting' || worker.state === 'waiting') ?? []
  return (
    <aside className={`sidebar${collapsed ? ' collapsed' : ''}`}>
      <div className="brand-row">
        <div className="brand-mark" aria-hidden="true">
          <img src={brandLogo} alt="" />
        </div>
        <div className="brand-copy"><strong>Codentum</strong><span>软件研发团队</span></div>
        <button className="icon-button sidebar-toggle" type="button" onClick={onToggle} aria-label={collapsed ? '展开侧栏' : '收起侧栏'}>
          <Icon name="menu" size={20} />
        </button>
      </div>

      <nav className="primary-nav" aria-label="主要功能">
        <button type="button" className={active === 'home' ? 'active' : ''} onClick={onNewTask} title={collapsed ? '新对话' : undefined}>
          <Icon name="plus" size={20} /><span>新对话</span>
        </button>
        {NAVIGATION.filter((item) => PRIMARY_NAV.has(item.id)).map((item) => (
          <button
            key={item.id}
            type="button"
            className={active === item.id ? 'active' : ''}
            disabled={item.id === 'delivery' && !validationEnabled}
            onClick={() => onNavigate(item.id)}
            title={collapsed ? item.label : undefined}
            aria-current={active === item.id ? 'page' : undefined}
          >
            <Icon name={item.icon as Parameters<typeof Icon>[0]['name']} size={20} />
            <span>{item.label}</span>
            {item.id === 'execution' && currentWorkers.length > 0 ? <em>{currentWorkers.length}</em> : null}
            {item.id === 'delivery' && !validationEnabled ? <em>待需求</em> : null}
          </button>
        ))}
      </nav>

      <section className="task-history-rail" aria-label="项目和最近任务">
        <div className="sidebar-group-heading">项目</div>
        <button type="button" className="sidebar-project-row" onClick={() => onNavigate('home')}>
          <Icon name="folder" size={16} />
          <span>{snapshot?.source.kind === 'project' ? snapshot.source.label : '没有项目'}</span>
        </button>
        <div className="sidebar-group-heading recent-heading"><span>最近</span><small>{tasks.length}</small></div>
        <div className="task-history-list">
          {tasks.length === 0 ? <p className="rail-empty">没有最近任务</p> : tasks.slice(0, 12).map((task) => (
            <button type="button" className={task.id === activeTaskId ? 'active' : ''} key={task.id} onClick={() => onSelectTask(task.id)}>
              <span><strong>{task.title}</strong></span>
            </button>
          ))}
        </div>
      </section>

      <details className="workspace-nav">
        <summary>项目视图</summary>
        <nav aria-label="项目视图">
          {NAVIGATION.filter((item) => PROJECT_NAV.has(item.id)).map((item) => (
            <button type="button" className={active === item.id ? 'active' : ''} key={item.id} onClick={() => onNavigate(item.id)}>
              <Icon name={item.icon as Parameters<typeof Icon>[0]['name']} size={17} /><span>{item.label}</span>
            </button>
          ))}
        </nav>
      </details>

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

      <nav className="sidebar-utility-nav" aria-label="设置与帮助">
        <button type="button" className={active === 'settings' || active === 'mcp' ? 'active' : ''} onClick={() => onNavigate('settings')} title={collapsed ? '设置' : undefined}><Icon name="settings" size={19} /><span>设置</span></button>
        <button type="button" className={active === 'help' ? 'active' : ''} onClick={() => onNavigate('help')} title={collapsed ? '帮助' : undefined}><Icon name="help" size={19} /><span>帮助</span></button>
      </nav>
      <div className="sidebar-footer">
        <span className={`connection-dot ${snapshot === null ? 'offline' : 'online'}`} />
        <div><strong>{snapshot?.source.label ?? '未选择项目'}</strong><small>{snapshot?.source.kind === 'fixture' ? '演示快照' : '本地状态'}</small></div>
      </div>
    </aside>
  )
}
