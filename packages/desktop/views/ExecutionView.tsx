import { useEffect, useMemo, useState, type ReactNode } from 'react'
import type { EngineHandshake, StateSnapshot, WorkerProjection } from '../shared/protocol'
import type { CommandDispatcher } from '../renderer/src/command-types'
import { projectWorkerModules, roleLabel, shortPacketId } from '../renderer/src/domain'
import { CommandPanel } from '../panels/CommandPanel'
import { EmptyState, Icon, PacketSummary, PageHeader } from '../panels/Common'

const WORKER_STATE_LABELS: Readonly<Record<WorkerProjection['state'], string>> = {
  starting: '正在启动',
  running: '执行中',
  waiting: '等待中',
  completed: '已完成',
  failed: '执行失败',
  aborted: '已停止',
  unknown: '状态未知'
}

function WorkerCard({ worker, selected, onSelect }: {
  readonly worker: WorkerProjection
  readonly selected: boolean
  readonly onSelect: () => void
}): ReactNode {
  const modules = projectWorkerModules(worker)
  const currentActivity = worker.currentModule ?? (
    worker.state === 'completed' ? '执行已完成'
      : worker.state === 'failed' ? '执行失败'
        : worker.state === 'aborted' ? '执行已停止'
          : modules.length > 0 ? '等待下一条模块事件' : '未提供模块投影'
  )
  return (
    <button type="button" className={`worker-card${selected ? ' selected' : ''}`} onClick={onSelect} aria-pressed={selected}>
      <span className={`worker-avatar worker-${worker.state}`}>{roleLabel(worker.role).slice(0, 1)}</span>
      <span className="worker-card-copy">
        <span><strong>{roleLabel(worker.role)}</strong><em className={`worker-state-label worker-${worker.state}`}>{WORKER_STATE_LABELS[worker.state]}</em></span>
        <small>{shortPacketId(worker.packetId)} · 第 {worker.attempt} 次尝试</small>
        <span className="worker-current">{currentActivity}</span>
      </span>
      <Icon name="chevron" size={18} />
    </button>
  )
}

function ModuleSequence({ worker, selectedModuleId, onSelectModule }: {
  readonly worker: WorkerProjection
  readonly selectedModuleId: string | null
  readonly onSelectModule: (moduleId: string) => void
}): ReactNode {
  const modules = projectWorkerModules(worker)
  if (modules.length === 0) {
    return <EmptyState title="尚未收到执行模块" detail="引擎没有在 Worker 事件中提供 moduleId。界面不会用预设动画或虚构步骤代替。" icon="pulse" />
  }
  return (
    <ol className="module-sequence">
      {modules.map((module, index) => (
        <li key={module.id} className={`module-${module.state}`}>
          <button type="button" className={selectedModuleId === module.id ? 'selected' : ''} onClick={() => onSelectModule(module.id)} aria-pressed={selectedModuleId === module.id}>
            <span className="module-index">{module.state === 'completed' ? <Icon name="check" size={17} /> : index + 1}</span>
            <span><strong>{module.label}</strong><small>{module.state === 'running' ? '当前模块' : module.state === 'completed' ? '引擎事件：已完成' : module.state === 'failed' ? '引擎事件：失败' : module.state === 'waiting' ? '引擎事件：等待' : '引擎事件：状态未知'}</small></span>
            <Icon name="chevron" size={17} />
          </button>
        </li>
      ))}
    </ol>
  )
}

function EventTimeline({ worker }: { readonly worker: WorkerProjection }): ReactNode {
  const events = [...worker.events].sort((left, right) => right.seq - left.seq).slice(0, 8)
  if (events.length === 0) return <p className="quiet-empty">这个 Worker 还没有可显示的运行事件。</p>
  return (
    <ol className="event-timeline">
      {events.map((event) => (
        <li key={`${event.seq}-${event.kind}`}>
          <span />
          <div><strong>{event.kind}</strong><small>序号 {event.seq} · {new Date(event.at).toLocaleString('zh-CN')}</small></div>
        </li>
      ))}
    </ol>
  )
}

export function ExecutionView({ snapshot, handshake, dispatch, focusedWorkerId, onFocusHandled }: {
  readonly snapshot: StateSnapshot | null
  readonly handshake: EngineHandshake
  readonly dispatch: CommandDispatcher
  readonly focusedWorkerId: string | null
  readonly onFocusHandled: () => void
}): ReactNode {
  const workers = snapshot?.workers ?? []
  const [selectedWorkerId, setSelectedWorkerId] = useState<string | null>(null)
  const [selectedModuleId, setSelectedModuleId] = useState<string | null>(null)
  const selectedWorker = useMemo(() => workers.find((worker) => worker.workerId === selectedWorkerId) ?? null, [workers, selectedWorkerId])

  useEffect(() => {
    if (selectedWorkerId !== null && !workers.some((worker) => worker.workerId === selectedWorkerId)) {
      setSelectedWorkerId(null)
      setSelectedModuleId(null)
    }
  }, [workers, selectedWorkerId])

  useEffect(() => {
    if (focusedWorkerId === null) return
    const worker = workers.find((candidate) => candidate.workerId === focusedWorkerId)
    if (worker !== undefined) {
      setSelectedWorkerId(worker.workerId)
      setSelectedModuleId(worker.currentModule ?? projectWorkerModules(worker)[0]?.id ?? null)
    }
    onFocusHandled()
  }, [focusedWorkerId, onFocusHandled, workers])

  function selectWorker(worker: WorkerProjection): void {
    setSelectedWorkerId(worker.workerId)
    setSelectedModuleId(worker.currentModule ?? projectWorkerModules(worker)[0]?.id ?? null)
  }

  return (
    <div className="page execution-view">
      <PageHeader
        eyebrow="Worker 事件投影"
        title="执行中心"
        description="读取 Worker manifest 与事件；运行控制只在引擎明确开放能力时出现。"
      />
      {snapshot?.source.kind === 'fixture' ? <div className="fixture-notice"><Icon name="warning" size={18} /><span>当前是演示快照。这里的数据不会控制任何真实 Agent。</span></div> : null}
      {snapshot === null ? (
        <EmptyState title="还没有项目状态" detail="打开本地项目后，这里会展示状态文件中的 Worker。" icon="folder" />
      ) : workers.length === 0 ? (
        <section className="projection-fallback">
          <EmptyState title="没有真实 Worker 投影" detail="项目只提供 WorkPacket 状态，因此下面明确标为“任务执行投影”，不把任务冒充 Agent。" icon="people" />
          {snapshot.packets.length > 0 ? (
            <div className="packet-projection-list">
              <h2>任务执行投影</h2>
              {snapshot.packets.map((packet) => <PacketSummary packet={packet} key={packet.id} />)}
            </div>
          ) : null}
        </section>
      ) : (
        <div className={`execution-layout${selectedWorker === null ? '' : ' has-panel'}`}>
          <aside className="worker-list">
            <div className="list-heading"><span>Worker</span><small>{workers.length}</small></div>
            {workers.map((worker) => <WorkerCard worker={worker} key={worker.workerId} selected={selectedWorkerId === worker.workerId} onSelect={() => selectWorker(worker)} />)}
          </aside>
          <main className="worker-detail">
            {selectedWorker === null ? (
              <EmptyState title="选择一个 Worker" detail="查看它由引擎事件投影出的执行模块和最近事件。" icon="pulse" />
            ) : (
              <>
                <section className="detail-card module-card">
                  <div className="card-heading"><div><span>执行模块</span><h2>{roleLabel(selectedWorker.role)}</h2></div><small>模块状态来自 Worker 事件</small></div>
                  <ModuleSequence worker={selectedWorker} selectedModuleId={selectedModuleId} onSelectModule={setSelectedModuleId} />
                </section>
                <section className="detail-card">
                  <div className="card-heading"><div><span>最近事件</span><h2>运行记录</h2></div><small>只显示最新 8 条</small></div>
                  <EventTimeline worker={selectedWorker} />
                </section>
              </>
            )}
          </main>
          {selectedWorker === null ? null : (
            <CommandPanel
              worker={selectedWorker}
              selectedModuleId={selectedModuleId}
              handshake={handshake}
              commandsAllowed={snapshot?.source.kind === 'project'}
              dispatch={dispatch}
              onClose={() => { setSelectedWorkerId(null); setSelectedModuleId(null) }}
            />
          )}
        </div>
      )}
    </div>
  )
}
