import type { ReactNode } from 'react'
import type { DecisionRecord, Evidence } from '@codentum/contracts'
import type { StateSnapshot } from '../shared/protocol'
import { roleLabel, shortPacketId } from '../renderer/src/domain'
import { EmptyState, Icon, PageHeader } from '../panels/Common'

function digestLabel(value: string): string {
  const digest = value.startsWith('sha256:') ? value.slice(7) : value
  return digest.length > 18 ? `${digest.slice(0, 10)}…${digest.slice(-8)}` : digest
}

function EvidenceRow({ evidence }: { readonly evidence: Evidence }): ReactNode {
  return (
    <article className="audit-row">
      <span className={`audit-verdict ${evidence.verdict}`}><Icon name={evidence.verdict === 'pass' ? 'check' : 'warning'} size={17} /></span>
      <div className="audit-row-main">
        <div><strong>{evidence.gate ?? evidence.kind}</strong><span>{roleLabel(evidence.role)}</span></div>
        <p>{evidence.detail ?? '状态源没有提供补充说明。'}</p>
        <small>{shortPacketId(evidence.packetId)} · {new Date(evidence.at).toLocaleString('zh-CN')}</small>
      </div>
      <dl className="audit-hashes">
        <div><dt>Digest</dt><dd title={evidence.digest}>{digestLabel(evidence.digest)}</dd></div>
        <div><dt>Prev</dt><dd title={evidence.prevDigest}>{digestLabel(evidence.prevDigest)}</dd></div>
      </dl>
    </article>
  )
}

function DecisionRow({ decision }: { readonly decision: DecisionRecord }): ReactNode {
  return (
    <article className="decision-row">
      <span className="decision-actor">{roleLabel(decision.actor)}</span>
      <div>
        <strong>{decision.action}</strong>
        <p>{decision.detail ?? decision.reasonCode}</p>
        <small>{decision.packetId === undefined ? '项目级决策' : shortPacketId(decision.packetId)} · {new Date(decision.at).toLocaleString('zh-CN')}</small>
      </div>
      <code>{decision.reasonCode}</code>
    </article>
  )
}

export function EvidenceView({ snapshot }: { readonly snapshot: StateSnapshot | null }): ReactNode {
  const evidence = [...(snapshot?.evidence ?? [])].sort((left, right) => right.at.localeCompare(left.at))
  const decisions = [...(snapshot?.decisions ?? [])].sort((left, right) => right.at.localeCompare(left.at))
  const failed = evidence.filter((item) => item.verdict === 'fail').length

  return (
    <main className="page evidence-view">
      <PageHeader
        eyebrow="只读状态投影"
        title="证据与审计"
        description="直接展示项目写入的证据和追加式决策记录；界面不代替控制平面判定，也不自行签发审计结论。"
      />
      {snapshot?.source.kind === 'fixture' ? <div className="fixture-notice"><Icon name="warning" size={18} /><span>这是演示快照中的证据与决策，不代表当前项目已经执行。</span></div> : null}
      {snapshot === null ? (
        <EmptyState title="尚未读取项目状态" detail="打开项目后，这里会读取 evidence/ 和 decisions.jsonl。" icon="shield" />
      ) : evidence.length === 0 && decisions.length === 0 ? (
        <EmptyState title="没有证据或审计记录" detail="状态源目前没有写入可展示的 evidence 或 decisions。" icon="shield" />
      ) : (
        <>
          <section className="audit-summary" aria-label="证据摘要">
            <div><strong>{evidence.length}</strong><span>证据记录</span></div>
            <div><strong>{decisions.length}</strong><span>决策记录</span></div>
            <div><strong>{failed}</strong><span>失败证据</span></div>
          </section>
          <div className="audit-columns">
            <section className="audit-section">
              <header><div><span>Evidence</span><h2>执行证据</h2></div><small>{evidence.length} 条</small></header>
              {evidence.length === 0 ? <p className="quiet-empty">项目尚未写入证据。</p> : evidence.map((item) => <EvidenceRow evidence={item} key={item.ref} />)}
            </section>
            <section className="audit-section">
              <header><div><span>Decisions</span><h2>决策日志</h2></div><small>{decisions.length} 条</small></header>
              {decisions.length === 0 ? <p className="quiet-empty">项目尚未写入决策日志。</p> : decisions.map((item, index) => <DecisionRow decision={item} key={`${item.at}-${item.action}-${index}`} />)}
            </section>
          </div>
          <p className="data-limitation"><Icon name="shield" size={17} />当前页面展示权威字段和摘要，不在浏览器中重新计算 digest；正式哈希链校验结果必须由确定性门禁写入证据。</p>
        </>
      )}
    </main>
  )
}
