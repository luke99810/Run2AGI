import type { ReactNode } from 'react'
import type { StateSnapshot } from '../shared/protocol'
import { packetTitle, shortPacketId } from '../renderer/src/domain'
import { EmptyState, Icon, PacketStateChip, PageHeader } from '../panels/Common'

export function DeliveryView({ snapshot, enabled = true }: { readonly snapshot: StateSnapshot | null; readonly enabled?: boolean }): ReactNode {
  const integrationPackets = snapshot?.packets.filter((packet) => packet.role === 'integrator' || packet.kind === 'integrate') ?? []
  const buildEvidence = snapshot?.evidence.filter((item) => item.kind === 'build') ?? []
  const testEvidence = snapshot?.evidence.filter((item) => item.kind === 'test_run' || item.kind === 'gate') ?? []
  if (!enabled) {
    return (
      <div className="page delivery-view delivery-disabled">
        <PageHeader eyebrow="按需启用" title="集成与验证" description="当前任务没有提出测试、验证、验收或集成要求。提出相关要求后，这里才会展示真实方法、进程与结果。" />
        <EmptyState title="等待验证需求" detail="该入口不会自动运行测试，也不会用演示结果代替真实验证。" icon="package" />
      </div>
    )
  }
  return (
    <div className="page delivery-view">
      <PageHeader eyebrow="状态源记录" title="集成与验证" description="汇总项目已经写入状态源的集成任务和测试、构建结果。" />
      {snapshot?.source.kind === 'fixture' ? <div className="fixture-notice"><Icon name="warning" size={18} /><span>这是演示快照，不能据此认定安装包已经生成。</span></div> : null}
      {snapshot === null ? (
        <EmptyState title="没有集成与验证记录" detail="打开项目后，这里会读取集成任务和测试、构建结果。" icon="package" />
      ) : (
        <>
          <div className="delivery-summary">
            <section><span className="delivery-icon"><Icon name="package" size={22} /></span><div><strong>{integrationPackets.length}</strong><span>集成任务</span></div></section>
            <section><span className="delivery-icon"><Icon name="check" size={22} /></span><div><strong>{testEvidence.filter((item) => item.verdict === 'pass').length}</strong><span>通过的测试/门禁记录</span></div></section>
            <section><span className="delivery-icon"><Icon name="pulse" size={22} /></span><div><strong>{buildEvidence.length}</strong><span>构建记录</span></div></section>
          </div>
          <div className="two-column-grid delivery-grid">
            <section className="detail-card">
              <div className="card-heading"><div><span>WorkPacket</span><h2>集成任务</h2></div></div>
              {integrationPackets.length === 0 ? <p className="quiet-empty">没有集成角色或 integrate 类型的任务。</p> : integrationPackets.map((packet) => (
                <article className="delivery-packet" key={packet.id}>
                  <div><strong>{packetTitle(packet)}</strong><small>{shortPacketId(packet.id)}</small></div>
                  <PacketStateChip state={packet.state} />
                </article>
              ))}
            </section>
            <section className="detail-card">
              <div className="card-heading"><div><span>状态源记录</span><h2>测试与构建</h2></div></div>
              {[...testEvidence, ...buildEvidence].length === 0 ? <p className="quiet-empty">状态源尚未写入测试或构建记录。</p> : [...testEvidence, ...buildEvidence].slice(0, 10).map((item) => (
                <article className="delivery-result" key={item.ref}>
                  <span className={item.verdict === 'pass' ? 'result-pass' : 'result-fail'}><Icon name={item.verdict === 'pass' ? 'check' : 'warning'} size={17} /></span>
                  <div><strong>{item.gate ?? item.kind}</strong><small>{shortPacketId(item.packetId)} · {new Date(item.at).toLocaleString('zh-CN')}</small></div>
                </article>
              ))}
            </section>
          </div>
        </>
      )}
    </div>
  )
}
