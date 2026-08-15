import { useMemo, useState, type ReactNode } from 'react'
import type { WorkPacket } from '@codentum/contracts'
import type { PacketFlowSegmentProjection, StateSnapshot } from '../shared/protocol'
import { buildFlowBoard, PACKET_STATE_LABELS, packetTitle, roleLabel, shortPacketId } from '../renderer/src/domain'
import { EmptyState, Icon, PacketStateChip, WarningNotice } from '../panels/Common'

export type LeanSchedulingMode = 'board' | 'value' | 'bottleneck'

export function LeanSchedulingView({ snapshot, mode }: {
  readonly snapshot: StateSnapshot | null
  readonly mode: LeanSchedulingMode
}): ReactNode {
  if (snapshot === null) {
    return <EmptyState title="还没有项目状态" detail="打开项目后才能读取 Packet 与可选的精益调度投影。" icon="folder" />
  }
  if (mode === 'value') return <ValueStreamView snapshot={snapshot} />
  if (mode === 'bottleneck') return <BottleneckView snapshot={snapshot} />
  return <FlowBoardView snapshot={snapshot} />
}

function FlowBoardView({ snapshot }: { readonly snapshot: StateSnapshot }): ReactNode {
  const [selectedPacketId, setSelectedPacketId] = useState<string | null>(null)
  const columns = useMemo(() => buildFlowBoard(snapshot), [snapshot])
  const selectedPacket = snapshot.packets.find((packet) => packet.id === selectedPacketId) ?? null
  const criticalPath = new Set(snapshot.scheduling?.criticalPath ?? [])
  const andons = new Map(snapshot.flow?.andons.map((andon) => [andon.packetId, andon]) ?? [])
  const readyPositions = new Map((snapshot.scheduling?.readyQueue ?? []).map((packetId, index) => [packetId, index + 1]))

  return (
    <section className="lean-scheduling-panel" aria-label="流动看板">
      {snapshot.scheduling === null ? (
        <WarningNotice message="A 尚未生成 scheduling.json；当前只按权威 PacketState 分列，WIP 上限、ready 顺序和关键路径不会由前端推算。" />
      ) : (
        <div className="projection-source-line">
          <span>调度投影 revision {snapshot.scheduling.revision ?? '未提供'}</span>
          <span>更新时间 {formatTimestamp(snapshot.scheduling.updatedAt)}</span>
        </div>
      )}
      <div className={`board-shell${selectedPacket === null ? '' : ' with-detail'}`}>
        <div className="kanban-scroll" tabIndex={0} aria-label="Packet 状态列，可横向滚动">
          {columns.map((column) => (
            <section className={`kanban-column column-${column.state}${column.overLimit ? ' wip-over-limit' : ''}`} key={column.state}>
              <header>
                <span>{column.label}</span>
                <small title={column.limit === undefined ? 'A 未提供 WIP 上限' : `WIP 上限 ${column.limit}`}>
                  {column.current} / {column.limit ?? '—'}
                </small>
              </header>
              <div className="kanban-cards">
                {column.packets.length === 0 ? <p>暂无任务</p> : column.packets.map((packet) => {
                  const andon = andons.get(packet.id)
                  const readyPosition = readyPositions.get(packet.id)
                  return (
                    <button
                      type="button"
                      className={`${selectedPacketId === packet.id ? 'selected ' : ''}${andon === undefined ? '' : `andon-${andon.severity}`}`.trim()}
                      key={packet.id}
                      onClick={() => setSelectedPacketId(packet.id)}
                      aria-pressed={selectedPacketId === packet.id}
                    >
                      <span className="card-kind">{roleLabel(packet.role)} · {packet.kind}</span>
                      <strong>{packetTitle(packet)}</strong>
                      <small>{shortPacketId(packet.id)} · 尝试 {packet.attempts}</small>
                      <span className="flow-card-flags">
                        {criticalPath.has(packet.id) ? <em><Icon name="pulse" size={13} />关键路径</em> : null}
                        {readyPosition === undefined ? null : <em>队列 #{readyPosition}</em>}
                        {andon === undefined ? null : <em className="andon-flag"><Icon name="warning" size={13} />安灯</em>}
                      </span>
                    </button>
                  )
                })}
              </div>
            </section>
          ))}
        </div>
        {selectedPacket === null ? null : (
          <PacketFlowDetail
            packet={selectedPacket}
            snapshot={snapshot}
            onClose={() => setSelectedPacketId(null)}
          />
        )}
      </div>
    </section>
  )
}

function PacketFlowDetail({ packet, snapshot, onClose }: {
  readonly packet: WorkPacket
  readonly snapshot: StateSnapshot
  readonly onClose: () => void
}): ReactNode {
  const flow = snapshot.flow?.packets.find((item) => item.packetId === packet.id)
  const andon = snapshot.flow?.andons.find((item) => item.packetId === packet.id)
  return (
    <aside className="packet-flow-detail" aria-label={`${packetTitle(packet)} 详情`}>
      <header><div><span>WorkPacket</span><h2>{packetTitle(packet)}</h2></div><button type="button" className="icon-button" onClick={onClose} aria-label="关闭任务详情"><Icon name="close" size={18} /></button></header>
      <PacketStateChip state={packet.state} />
      <dl>
        <div><dt>Packet</dt><dd>{packet.id}</dd></div>
        <div><dt>角色</dt><dd>{roleLabel(packet.role)}</dd></div>
        <div><dt>尝试</dt><dd>{packet.attempts}</dd></div>
        <div><dt>总周期</dt><dd>{flow === undefined ? 'A/B 未提供' : formatDuration(flow.totalCycleMs)}</dd></div>
        <div><dt>流动效率</dt><dd>{formatRatio(flow?.efficiency)}</dd></div>
      </dl>
      {andon === undefined ? null : <div className={`andon-detail andon-${andon.severity}`}><strong><Icon name="warning" size={16} />安灯异常</strong><p>{andon.reason}</p><small>{formatTimestamp(andon.at)}{andon.consecutiveFailures === undefined ? '' : ` · 连续失败 ${andon.consecutiveFailures} 次`}</small></div>}
      {flow === undefined || flow.segments.length === 0 ? <p className="quiet-empty">尚无该 Packet 的价值流记录。</p> : <FlowSegments segments={flow.segments} totalMs={flow.totalCycleMs} />}
    </aside>
  )
}

function ValueStreamView({ snapshot }: { readonly snapshot: StateSnapshot }): ReactNode {
  const flow = snapshot.flow
  if (flow === null) {
    return <EmptyState title="价值流数据尚未生成" detail="等待 A/B 写入经过契约校验的 .codentum/flow.json；C 不使用文件修改时间推算状态耗时。" icon="waves" />
  }
  return (
    <section className="lean-scheduling-panel value-stream-view">
      <div className="flow-summary">
        <div><span>系统流动效率</span><strong>{formatRatio(flow.efficiency)}</strong></div>
        <div><span>已记录 Packet</span><strong>{flow.packets.length}</strong></div>
        <div><span>计算时间</span><strong>{formatTimestamp(flow.calculatedAt)}</strong></div>
      </div>
      {flow.packets.length === 0 ? <EmptyState title="没有 Packet 价值流" detail="flow.json 已存在，但没有 packet 级时间分布。" icon="clock" /> : (
        <div className="value-stream-list">
          {flow.packets.map((packet) => {
            const source = snapshot.packets.find((candidate) => candidate.id === packet.packetId)
            return <article key={packet.packetId}><header><div><strong>{source === undefined ? packet.packetId : packetTitle(source)}</strong><small>{shortPacketId(packet.packetId)}</small></div><span>{formatDuration(packet.totalCycleMs)} · {formatRatio(packet.efficiency)}</span></header><FlowSegments segments={packet.segments} totalMs={packet.totalCycleMs} /></article>
          })}
        </div>
      )}
    </section>
  )
}

function FlowSegments({ segments, totalMs }: { readonly segments: readonly PacketFlowSegmentProjection[]; readonly totalMs: number }): ReactNode {
  const denominator = Math.max(totalMs, segments.reduce((sum, segment) => sum + segment.durationMs, 0), 1)
  return (
    <ol className="flow-segments">
      {segments.map((segment, index) => (
        <li key={`${segment.state}-${segment.kind}-${index}`}>
          <span>{PACKET_STATE_LABELS[segment.state]}</span>
          <span className="flow-segment-track"><i className={`segment-${segment.kind}`} style={{ width: `${Math.max(2, (segment.durationMs / denominator) * 100)}%` }} /></span>
          <strong>{formatDuration(segment.durationMs)}</strong>
          <small>{segment.reason ?? (segment.kind === 'value' ? '增值时间' : '等待时间')}</small>
        </li>
      ))}
    </ol>
  )
}

function BottleneckView({ snapshot }: { readonly snapshot: StateSnapshot }): ReactNode {
  const flow = snapshot.flow
  if (flow === null || flow.bottleneck === undefined) {
    return <EmptyState title="瓶颈诊断尚未生成" detail="等待 A 的确定性流动计算输出瓶颈状态、p80 等待时间和建议动作。" icon="pulse" />
  }
  return (
    <section className="lean-scheduling-panel bottleneck-view">
      <div className="bottleneck-hero"><span>当前约束</span><h2>{PACKET_STATE_LABELS[flow.bottleneck.state]}</h2><p>p80 等待 {formatDuration(flow.bottleneck.waitP80Ms)} · 影响 {flow.bottleneck.affectedPackets} 个 Packet</p>{flow.bottleneck.recommendation === undefined ? null : <blockquote>{flow.bottleneck.recommendation}</blockquote>}</div>
      <div className="bottleneck-grid">
        <section><header><strong>状态等待分布</strong><span>{flow.stages.length} 个状态</span></header>{flow.stages.length === 0 ? <p className="quiet-empty">未提供状态分布。</p> : <ol>{flow.stages.map((stage) => <li key={stage.state}><span>{PACKET_STATE_LABELS[stage.state]}</span><strong>p80 {stage.waitP80Ms === undefined ? '未提供' : formatDuration(stage.waitP80Ms)}</strong><small>{stage.packetCount} 个 Packet</small></li>)}</ol>}</section>
        <section><header><strong>安灯异常</strong><span>{flow.andons.length}</span></header>{flow.andons.length === 0 ? <p className="quiet-empty">当前没有显式安灯记录。</p> : <ol className="andon-list">{flow.andons.map((andon) => <li className={`andon-${andon.severity}`} key={andon.id}><Icon name="warning" size={17} /><div><strong>{shortPacketId(andon.packetId)}</strong><p>{andon.reason}</p><small>{formatTimestamp(andon.at)}</small></div></li>)}</ol>}</section>
      </div>
    </section>
  )
}

function formatDuration(milliseconds: number): string {
  if (milliseconds < 1_000) return `${Math.round(milliseconds)}ms`
  const totalSeconds = Math.round(milliseconds / 1_000)
  if (totalSeconds < 60) return `${totalSeconds}秒`
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  if (minutes < 60) return seconds === 0 ? `${minutes}分钟` : `${minutes}分 ${seconds}秒`
  const hours = Math.floor(minutes / 60)
  return `${hours}小时 ${minutes % 60}分`
}

function formatRatio(value: number | undefined): string {
  return value === undefined ? '未提供' : `${(value * 100).toFixed(1)}%`
}

function formatTimestamp(value: string | undefined): string {
  if (value === undefined) return '未提供'
  const timestamp = Date.parse(value)
  return Number.isNaN(timestamp) ? value : new Date(timestamp).toLocaleString('zh-CN')
}
