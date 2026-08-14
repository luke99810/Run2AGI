import { useState, type ReactNode } from 'react'
import type { ArtifactPackageResult, StateSnapshot } from '../shared/protocol'
import { packetTitle, shortPacketId } from '../renderer/src/domain'
import { EmptyState, Icon, PacketStateChip, PageHeader } from '../panels/Common'

export function DeliveryView({ snapshot, sourceId, packageArtifact, enabled = true }: {
  readonly snapshot: StateSnapshot | null
  readonly sourceId: string | null
  readonly packageArtifact: (sourceId: string, suggestedName: string, packetId?: string) => Promise<ArtifactPackageResult | null>
  readonly enabled?: boolean
}): ReactNode {
  const [packaging, setPackaging] = useState(false)
  const [packageResult, setPackageResult] = useState<ArtifactPackageResult | null>(null)
  const [packageError, setPackageError] = useState<string | null>(null)
  const [packetId, setPacketId] = useState('')
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
          <section className="detail-card artifact-package-card">
            <div className="card-heading"><div><span>真实文件产物</span><h2>项目源码交付包</h2></div></div>
            <p>生成当前已绑定项目的 <code>.tar.gz</code> 源码包，附逐文件 SHA-256 清单，并在临时隔离目录完成解包复核。它不是 EXE 或安装包，也不代表 Agent 已完成构建。</p>
            <div className="artifact-package-controls">
              <label><span>关联 WorkPacket（可选）</span><select value={packetId} onChange={(event) => setPacketId(event.target.value)}><option value="">不关联</option>{snapshot.packets.map((packet) => <option key={packet.id} value={packet.id}>{packetTitle(packet)} · {shortPacketId(packet.id)}</option>)}</select></label>
              <button type="button" className="primary-button" disabled={packaging || sourceId === null || snapshot.source.kind !== 'project'} onClick={() => {
                if (sourceId === null) return
                setPackaging(true)
                setPackageError(null)
                void packageArtifact(sourceId, snapshot.source.label, packetId || undefined)
                  .then((result) => { if (result !== null) setPackageResult(result) })
                  .catch((error: unknown) => setPackageError(error instanceof Error ? error.message : String(error)))
                  .finally(() => setPackaging(false))
              }}><Icon name="package" size={17} />{packaging ? '正在打包并验证…' : '生成源码交付包'}</button>
            </div>
            {packageError === null ? null : <div className="resource-error" role="alert"><Icon name="warning" size={17} />{packageError}</div>}
            {packageResult === null ? null : <div className="artifact-package-result">
              <div><strong>{packageResult.fileName}</strong><span>{packageResult.verified ? '隔离解包验证通过' : '验证未通过'}</span></div>
              <dl><div><dt>文件</dt><dd>{packageResult.fileCount}</dd></div><div><dt>源码大小</dt><dd>{formatBytes(packageResult.sourceBytes)}</dd></div><div><dt>压缩包</dt><dd>{formatBytes(packageResult.archiveBytes)}</dd></div><div><dt>SHA-256</dt><dd title={packageResult.sha256}>{packageResult.sha256.slice(0, 16)}…</dd></div></dl>
              <details><summary>查看打包与验证日志</summary><ol>{packageResult.log.map((line, index) => <li key={`${index}-${line}`}>{line}</li>)}</ol></details>
            </div>}
          </section>
        </>
      )}
    </div>
  )
}

function formatBytes(value: number): string {
  if (value < 1_024) return `${value} B`
  if (value < 1_048_576) return `${(value / 1_024).toFixed(1)} KiB`
  return `${(value / 1_048_576).toFixed(1)} MiB`
}
