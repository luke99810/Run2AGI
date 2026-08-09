import { useState, type ReactNode } from 'react'
import type { WorkPacket } from '@codentum/contracts'
import type { StateSnapshot } from '../shared/protocol'
import { PACKET_STATES, PACKET_STATE_LABELS, formatCny, packetTitle, roleLabel, shortPacketId } from '../renderer/src/domain'
import { EmptyState, Icon, PacketStateChip, PageHeader } from '../panels/Common'

function PacketDetail({ packet, onClose }: { readonly packet: WorkPacket; readonly onClose: () => void }): ReactNode {
  return (
    <aside className="read-detail-panel">
      <header><div><span>任务详情</span><h2>{packetTitle(packet)}</h2></div><button type="button" className="icon-button" onClick={onClose} aria-label="关闭详情"><Icon name="close" size={19} /></button></header>
      <PacketStateChip state={packet.state} />
      <dl className="detail-list">
        <div><dt>任务编号</dt><dd>{packet.id}</dd></div>
        <div><dt>负责角色</dt><dd>{roleLabel(packet.role)}</dd></div>
        <div><dt>任务类型</dt><dd>{packet.kind}</dd></div>
        <div><dt>尝试次数</dt><dd>{packet.attempts}</dd></div>
        <div><dt>预算</dt><dd>{formatCny(packet.budget.spentCny)} / {formatCny(packet.budget.limitCny)}</dd></div>
        <div><dt>依赖</dt><dd>{packet.deps.length === 0 ? '无' : packet.deps.join('、')}</dd></div>
        <div><dt>负责路径</dt><dd>{packet.ownsPaths.length === 0 ? '未声明' : packet.ownsPaths.join('、')}</dd></div>
        <div><dt>完成判据</dt><dd><code>{packet.acceptance.predicate}</code></dd></div>
      </dl>
    </aside>
  )
}

export function BoardView({ snapshot }: { readonly snapshot: StateSnapshot | null }): ReactNode {
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const packets = snapshot?.packets ?? []
  const selected = packets.find((packet) => packet.id === selectedId) ?? null
  return (
    <div className="page board-view">
      <PageHeader
        eyebrow="WorkPacket 状态"
        title="任务看板"
        description="按控制平面的权威状态排列任务。当前契约没有 WIP 上限，因此不显示虚构限额。"
      />
      {snapshot?.source.kind === 'fixture' ? <div className="fixture-notice"><Icon name="warning" size={18} /><span>演示快照中的任务不会随界面操作改变。</span></div> : null}
      {snapshot === null || packets.length === 0 ? (
        <EmptyState title="暂无任务" detail="选择包含 WorkPacket 的项目状态后，任务会出现在对应列。" icon="board" />
      ) : (
        <div className={`board-shell${selected === null ? '' : ' with-detail'}`}>
          <div className="kanban-scroll" aria-label="WorkPacket 看板">
            {PACKET_STATES.map((state) => {
              const statePackets = packets.filter((packet) => packet.state === state)
              return (
                <section className={`kanban-column column-${state}`} key={state}>
                  <header><span>{PACKET_STATE_LABELS[state]}</span><small>{statePackets.length}</small></header>
                  <div className="kanban-cards">
                    {statePackets.length === 0 ? <p>暂无任务</p> : statePackets.map((packet) => (
                      <button type="button" className={selectedId === packet.id ? 'selected' : ''} key={packet.id} onClick={() => setSelectedId(packet.id)} aria-pressed={selectedId === packet.id}>
                        <span className="card-kind">{packet.kind}</span>
                        <strong>{packetTitle(packet)}</strong>
                        <small>{roleLabel(packet.role)} · {shortPacketId(packet.id)}</small>
                        <div className="card-budget"><span style={{ width: `${Math.min(100, packet.budget.limitCny === 0 ? 0 : packet.budget.spentCny / packet.budget.limitCny * 100)}%` }} /><em>{formatCny(packet.budget.spentCny)}</em></div>
                      </button>
                    ))}
                  </div>
                </section>
              )
            })}
          </div>
          {selected === null ? null : <PacketDetail packet={selected} onClose={() => setSelectedId(null)} />}
        </div>
      )}
    </div>
  )
}
