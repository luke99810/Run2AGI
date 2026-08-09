import type { ReactNode } from 'react'
import type { StateSnapshot } from '../shared/protocol'
import { buildDependencyWaves, packetTitle, roleLabel, shortPacketId } from '../renderer/src/domain'
import { EmptyState, ErrorNotice, Icon, PacketStateChip, PageHeader } from '../panels/Common'

export function WavesView({ snapshot }: { readonly snapshot: StateSnapshot | null }): ReactNode {
  const result = snapshot === null ? null : buildDependencyWaves(snapshot)
  const packetById = new Map(snapshot?.packets.map((packet) => [packet.id as string, packet]) ?? [])
  return (
    <div className="page waves-view">
      <PageHeader
        eyebrow="拓扑顺序 · 不是时间计划"
        title="依赖波次"
        description="波次只表示依赖层级，不代表调度已批准并行。当前契约没有计划和实际时间。"
      />
      {snapshot?.source.kind === 'fixture' ? <div className="fixture-notice"><Icon name="warning" size={18} /><span>当前波次根据演示快照中的依赖边计算。</span></div> : null}
      {result === null || (result.waves.length === 0 && result.unresolved.length === 0) ? (
        <EmptyState title="没有依赖层级" detail="项目提供 graph.json 或任务依赖后，这里会按拓扑顺序计算波次。" icon="waves" />
      ) : (
        <>
          {result.unresolved.length > 0 ? <ErrorNotice message={`依赖图存在无法解析的节点：${result.unresolved.join('、')}。不会把环形依赖展示成可执行计划。`} /> : null}
          <div className="wave-track">
            {result.waves.map((wave, index) => (
              <section className="wave-column" key={`wave-${index + 1}`}>
                <header><span>波次 {index + 1}</span><small>{wave.length} 个任务</small></header>
                <div>
                  {wave.map((packetId) => {
                    const packet = packetById.get(packetId)
                    return (
                      <article className="wave-card" key={packetId}>
                        {packet === undefined ? (
                          <><strong>{shortPacketId(packetId)}</strong><small>依赖图包含此节点，但没有任务文件</small></>
                        ) : (
                          <>
                            <PacketStateChip state={packet.state} />
                            <strong>{packetTitle(packet)}</strong>
                            <small>{roleLabel(packet.role)} · {shortPacketId(packet.id)}</small>
                          </>
                        )}
                      </article>
                    )
                  })}
                </div>
                {index < result.waves.length - 1 ? <span className="wave-arrow"><Icon name="chevron" size={19} /></span> : null}
              </section>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
