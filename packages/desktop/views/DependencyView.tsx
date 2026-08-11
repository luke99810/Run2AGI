import { useMemo, useState, type KeyboardEvent, type ReactNode } from 'react'
import type { WorkPacket } from '@codentum/contracts'
import type { StateSnapshot } from '../shared/protocol'
import { buildDependencyWaves, packetTitle, roleLabel, shortPacketId } from '../renderer/src/domain'
import { EmptyState, Icon, PacketStateChip, PageHeader } from '../panels/Common'

interface NodePosition {
  readonly id: string
  readonly x: number
  readonly y: number
}

export function DependencyView({ snapshot }: { readonly snapshot: StateSnapshot | null }): ReactNode {
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const packetById = useMemo(() => new Map(snapshot?.packets.map((packet) => [packet.id as string, packet]) ?? []), [snapshot])
  const result = snapshot === null ? null : buildDependencyWaves(snapshot)
  const positions = useMemo(() => {
    const next: NodePosition[] = []
    for (const [waveIndex, wave] of (result?.waves ?? []).entries()) {
      for (const [rowIndex, id] of wave.entries()) next.push({ id, x: 110 + waveIndex * 250, y: 70 + rowIndex * 110 })
    }
    return next
  }, [result])
  const positionById = new Map(positions.map((position) => [position.id, position]))
  const edges = snapshot?.graph?.dependency.edges ?? snapshot?.packets.flatMap((packet) => packet.deps.map((dependency) => ({ from: dependency, to: packet.id }))) ?? []
  const graphWidth = Math.max(520, (result?.waves.length ?? 0) * 250 + 30)
  const graphHeight = Math.max(300, ...positions.map((position) => position.y + 70))
  const selected = selectedId === null ? undefined : packetById.get(selectedId)

  function keyboardSelect(event: KeyboardEvent<SVGGElement>, id: string): void {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      setSelectedId(id)
    }
  }

  return (
    <div className="page dependency-view">
      <PageHeader
        eyebrow="任务 DAG"
        title="依赖关系"
        description="节点和连线直接来自项目依赖图。"
      />
      {snapshot?.source.kind === 'fixture' ? <div className="fixture-notice"><Icon name="warning" size={18} /><span>这是演示快照中的依赖关系。</span></div> : null}
      {positions.length === 0 ? (
        <EmptyState title="没有依赖节点" detail="项目的 graph.json 目前没有可以绘制的任务节点。" icon="graph" />
      ) : (
        <div className="dependency-layout">
          <div className="graph-canvas" tabIndex={0} aria-label="任务依赖图，可横向滚动">
            <svg width={graphWidth} height={graphHeight} viewBox={`0 0 ${graphWidth} ${graphHeight}`} role="img" aria-label="WorkPacket 依赖关系">
              <defs><marker id="arrowhead" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" /></marker></defs>
              <g className="dependency-edges">
                {edges.map((edge, index) => {
                  const from = positionById.get(edge.from)
                  const to = positionById.get(edge.to)
                  if (from === undefined || to === undefined) return null
                  const startX = from.x + 80
                  const endX = to.x - 80
                  const control = (startX + endX) / 2
                  return <path key={`${edge.from}-${edge.to}-${index}`} d={`M ${startX} ${from.y} C ${control} ${from.y}, ${control} ${to.y}, ${endX} ${to.y}`} markerEnd="url(#arrowhead)" />
                })}
              </g>
              <g className="dependency-nodes">
                {positions.map((position) => {
                  const packet = packetById.get(position.id)
                  const state = packet?.state ?? 'missing'
                  return (
                    <g
                      className={`dependency-node node-${state}${selectedId === position.id ? ' selected' : ''}`}
                      key={position.id}
                      role="button"
                      tabIndex={0}
                      aria-label={`任务 ${position.id}`}
                      transform={`translate(${position.x - 80} ${position.y - 35})`}
                      onClick={() => setSelectedId(position.id)}
                      onKeyDown={(event) => keyboardSelect(event, position.id)}
                    >
                      <rect width="160" height="70" rx="8" />
                      <circle cx="17" cy="18" r="5" />
                      <text x="29" y="22" className="node-role">{packet === undefined ? '缺少任务文件' : roleLabel(packet.role)}</text>
                      <text x="14" y="47" className="node-title">{packet === undefined ? shortPacketId(position.id) : packetTitle(packet).slice(0, 20)}</text>
                      <text x="14" y="62" className="node-id">{shortPacketId(position.id)}</text>
                    </g>
                  )
                })}
              </g>
            </svg>
          </div>
          <aside className="graph-inspector">
            {selectedId === null ? (
              <EmptyState title="选择一个任务" detail="查看任务状态、前置依赖和负责路径。" icon="graph" />
            ) : selected === undefined ? (
              <MissingDependencyInspector nodeId={selectedId} />
            ) : (
              <DependencyInspector packet={selected} locks={snapshot?.graph?.ownership.locks ?? []} ownershipVersion={snapshot?.graph?.ownership.version} />
            )}
          </aside>
        </div>
      )}
    </div>
  )
}

function DependencyInspector({ packet, locks, ownershipVersion }: {
  readonly packet: WorkPacket
  readonly locks: readonly { readonly pathPrefix: string; readonly heldBy: string; readonly acquiredAt: string }[]
  readonly ownershipVersion: number | undefined
}): ReactNode {
  const heldLocks = locks.filter((lock) => lock.heldBy === packet.id)
  return (
    <div className="inspector-content">
      <div><span>任务节点</span><h2>{packetTitle(packet)}</h2></div>
      <PacketStateChip state={packet.state} />
      <dl className="detail-list">
        <div><dt>负责角色</dt><dd>{roleLabel(packet.role)}</dd></div>
        <div><dt>前置依赖</dt><dd>{packet.deps.length === 0 ? '状态源未声明' : packet.deps.join('、')}</dd></div>
        <div><dt>负责路径</dt><dd>{packet.ownsPaths.length === 0 ? '未声明' : packet.ownsPaths.join('、')}</dd></div>
        <div><dt>只读路径</dt><dd>{packet.readsPaths.length === 0 ? '无' : packet.readsPaths.join('、')}</dd></div>
        <div><dt>验收类型</dt><dd>{packet.acceptance.kind} · 由 {roleLabel(packet.acceptance.authoredBy)} 编写</dd></div>
        <div><dt>验收判据</dt><dd><code>{packet.acceptance.predicate}</code>{packet.acceptance.threshold === undefined ? null : ` · 阈值 ${packet.acceptance.threshold}`}</dd></div>
        <div><dt>任务预算</dt><dd>¥{packet.budget.spentCny.toFixed(2)} / ¥{packet.budget.limitCny.toFixed(2)}</dd></div>
        <div><dt>降级链</dt><dd>{packet.budget.degradationChain.length === 0 ? '未声明' : packet.budget.degradationChain.join(' → ')}</dd></div>
        <div><dt>模型路由</dt><dd>{packet.routing === undefined ? '使用角色默认策略' : `${packet.routing.model} · ${packet.routing.effort}${packet.routing.batch === true ? ' · Batch' : ''}`}</dd></div>
        <div><dt>尝试次数</dt><dd>{packet.attempts}</dd></div>
        <div><dt>证据引用</dt><dd>{packet.evidence.length === 0 ? '尚无' : packet.evidence.join('、')}</dd></div>
        <div><dt>创建来源</dt><dd>{roleLabel(packet.provenance.createdBy)} · {new Date(packet.provenance.createdAt).toLocaleString('zh-CN')}{packet.provenance.parent === undefined ? '' : ` · 父任务 ${packet.provenance.parent}`}</dd></div>
        <div><dt>所有权锁</dt><dd>{heldLocks.length === 0 ? '当前未持有路径锁' : heldLocks.map((lock) => `${lock.pathPrefix}（${new Date(lock.acquiredAt).toLocaleString('zh-CN')}）`).join('、')}</dd></div>
        <div><dt>锁版本</dt><dd>{ownershipVersion ?? '状态源未提供'}</dd></div>
      </dl>
    </div>
  )
}

function MissingDependencyInspector({ nodeId }: { readonly nodeId: string }): ReactNode {
  return (
    <div className="inspector-content missing-node-detail">
      <div><span>依赖图节点</span><h2>{nodeId}</h2></div>
      <div className="panel-unavailable"><Icon name="warning" size={18} />依赖图包含此节点，但 packets 中没有对应的 WorkPacket。</div>
    </div>
  )
}
