import type { ReactNode } from 'react'
import type { StateSnapshot } from '../shared/protocol'
import { formatCny, roleLabel } from '../renderer/src/domain'
import { EmptyState, Icon, PageHeader } from '../panels/Common'

function CostBreakdown({ title, entries, total, roleNames = false }: {
  readonly title: string
  readonly entries: readonly (readonly [string, number])[]
  readonly total: number
  readonly roleNames?: boolean
}): ReactNode {
  const maximum = Math.max(0, ...entries.map(([, amount]) => amount))
  return (
    <section className="cost-breakdown detail-card">
      <div className="card-heading"><div><span>实际支出</span><h2>{title}</h2></div><small>{entries.length} 项</small></div>
      {entries.length === 0 ? <p className="quiet-empty">预算文件没有提供此维度。</p> : (
        <div className="cost-bars">
          {entries.map(([key, amount]) => (
            <div className="cost-row" key={key}>
              <div><strong>{roleNames ? roleLabel(key) : key}</strong><span>{formatCny(amount)}</span></div>
              <div className="cost-track"><span style={{ width: `${maximum === 0 ? 0 : amount / maximum * 100}%` }} /></div>
              <small>{total === 0 ? '0%' : `${Math.round(amount / total * 100)}%`}</small>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}

export function CostView({ snapshot }: { readonly snapshot: StateSnapshot | null }): ReactNode {
  const budget = snapshot?.budget
  if (budget === null || budget === undefined) {
    return (
      <div className="page cost-view">
        <PageHeader eyebrow="人民币口径" title="成本" description="按项目预算文件展示真实花费，不使用 token 数替代金额。" />
        <EmptyState title="没有成本数据" detail="项目提供 .codentum/budget.json 后，这里会显示人民币预算和分项支出。" icon="wallet" />
      </div>
    )
  }
  const usedPercent = budget.limitCny === 0 ? 0 : Math.min(100, budget.spentCny / budget.limitCny * 100)
  const byRole = Object.entries(budget.byRole ?? {}).sort((left, right) => right[1] - left[1])
  const byModel = Object.entries(budget.byModel ?? {}).sort((left, right) => right[1] - left[1])
  return (
    <div className="page cost-view">
      <PageHeader eyebrow="人民币口径" title="成本" description="预算、支出和分摊均读取项目中的权威 budget.json。" />
      {snapshot?.source.kind === 'fixture' ? <div className="fixture-notice"><Icon name="warning" size={18} /><span>以下金额来自演示快照，不是实际账单。</span></div> : null}
      <section className="budget-hero">
        <div className="budget-ring" style={{ '--budget-progress': `${usedPercent * 3.6}deg` } as React.CSSProperties}>
          <div><strong>{Math.round(usedPercent)}%</strong><span>预算使用</span></div>
        </div>
        <div className="budget-copy">
          <span>本项目已发生成本</span>
          <h2>{formatCny(budget.spentCny)}</h2>
          <p>预算上限 {formatCny(budget.limitCny)}，剩余 {formatCny(Math.max(0, budget.limitCny - budget.spentCny))}</p>
          <div className="budget-meter"><span style={{ width: `${usedPercent}%` }} /></div>
        </div>
        <div className="budget-alerts">
          <span>预算提醒</span>
          {budget.alerts === undefined || budget.alerts.length === 0 ? <strong className="ok-copy"><Icon name="check" size={18} />当前没有告警</strong> : budget.alerts.map((alert) => <strong className={alert.level === 'hard_stop' ? 'danger-copy' : 'warn-copy'} key={`${alert.at}-${alert.message}`}><Icon name="warning" size={18} />{alert.message}</strong>)}
        </div>
      </section>
      <div className="two-column-grid">
        <CostBreakdown title="按 Agent 角色" entries={byRole} total={budget.spentCny} roleNames />
        <CostBreakdown title="按模型" entries={byModel} total={budget.spentCny} />
      </div>
      <p className="data-limitation"><Icon name="clock" size={17} />当前契约没有“按阶段”成本字段，因此这里不按比例推算，也不展示虚构阶段成本。</p>
    </div>
  )
}
