import type { ReactNode } from 'react'
import type { EngineHandshake, ProjectSelectionKind, SnapshotSourceDescriptor, StateSnapshot } from '../shared/protocol'
import { Icon } from './Common'

export function Topbar({ sources, selectedSourceId, snapshot, handshake, loading, onSelectSource, onSelectProject, onRefresh }: {
  readonly sources: readonly SnapshotSourceDescriptor[]
  readonly selectedSourceId: string | null
  readonly snapshot: StateSnapshot | null
  readonly handshake: EngineHandshake
  readonly loading: boolean
  readonly onSelectSource: (sourceId: string) => void
  readonly onSelectProject: (kind: ProjectSelectionKind) => Promise<void>
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
        <details className="project-open-menu">
          <summary className="secondary-button compact-button">
            <Icon name="folder" size={18} />打开项目<Icon name="chevron" size={14} />
          </summary>
          <div>
            <button type="button" onClick={(event) => {
              event.currentTarget.closest('details')?.removeAttribute('open')
              void onSelectProject('file')
            }}>
              <Icon name="file" size={18} />
              <span><strong>打开文件</strong><small>选择任意文件，以所在目录作为工作区</small></span>
            </button>
            <button type="button" onClick={(event) => {
              event.currentTarget.closest('details')?.removeAttribute('open')
              void onSelectProject('folder')
            }}>
              <Icon name="folder" size={18} />
              <span><strong>打开文件夹</strong><small>选择任意文件夹作为工作区</small></span>
            </button>
          </div>
        </details>
        <button type="button" className="icon-button" onClick={() => void onRefresh()} aria-label="刷新状态" disabled={loading}>
          <Icon name="refresh" size={19} />
        </button>
      </div>
    </header>
  )
}
