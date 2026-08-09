import type { ReactNode } from 'react'
import type { EngineHandshake, SnapshotSourceDescriptor, StateSnapshot } from '../shared/protocol'
import { Icon } from './Common'

export function Topbar({ sources, selectedSourceId, snapshot, handshake, loading, onSelectSource, onSelectProject, onRefresh }: {
  readonly sources: readonly SnapshotSourceDescriptor[]
  readonly selectedSourceId: string | null
  readonly snapshot: StateSnapshot | null
  readonly handshake: EngineHandshake
  readonly loading: boolean
  readonly onSelectSource: (sourceId: string) => void
  readonly onSelectProject: () => Promise<void>
  readonly onRefresh: () => Promise<void>
}): ReactNode {
  return (
    <header className="topbar">
      <div className="source-picker">
        <Icon name="folder" size={19} />
        <select
          aria-label="状态来源"
          value={selectedSourceId ?? ''}
          onChange={(event) => onSelectSource(event.target.value)}
          disabled={sources.length === 0}
        >
          {sources.length === 0 ? <option value="">没有可用来源</option> : null}
          {sources.map((source) => <option value={source.id} key={source.id}>{source.label}</option>)}
        </select>
        {snapshot?.source.kind === 'fixture' ? <span className="fixture-badge">演示快照 · 非实时</span> : null}
      </div>
      <div className="topbar-actions">
        <span className={`engine-pill ${handshake.connected ? 'connected' : 'disconnected'}`} title={handshake.unavailableReason}>
          <span />{handshake.connected ? `引擎 ${handshake.engineVersion}` : '引擎未连接'}
        </span>
        <button type="button" className="secondary-button compact-button" onClick={() => void onSelectProject()}>
          <Icon name="folder" size={18} />打开项目
        </button>
        <button type="button" className="icon-button" onClick={() => void onRefresh()} aria-label="刷新状态" disabled={loading}>
          <Icon name="refresh" size={19} />
        </button>
      </div>
    </header>
  )
}
