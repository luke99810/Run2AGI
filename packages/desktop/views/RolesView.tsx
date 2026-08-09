import type { ReactNode } from 'react'
import type { RoleId, RoleSpec } from '@codentum/contracts'
import type { StateSnapshot } from '../shared/protocol'
import { formatCny, ROLE_ROSTER, roleLabel, type RoleRosterEntry } from '../renderer/src/domain'
import { Icon, PageHeader } from '../panels/Common'

function RoleCard({ entry, role, currentWorkers, taskCount }: {
  readonly entry: RoleRosterEntry
  readonly role: RoleSpec | undefined
  readonly currentWorkers: number
  readonly taskCount: number
}): ReactNode {
  const configured = role !== undefined
  return (
    <article className={`role-card${configured ? '' : ' unconfigured'}`}>
      <header>
        <span className="role-avatar">{roleLabel(entry.id).slice(0, 1)}</span>
        <div><h2>{roleLabel(entry.id)}</h2><span>{entry.id}</span></div>
        {configured
          ? <span className={`model-badge ${role.usesModel ? '' : 'deterministic'}`}>{role.usesModel ? '模型岗位' : '确定性执行'}</span>
          : <span className="model-badge unconfigured">未配置</span>}
      </header>
      <p>{role?.summary ?? entry.summary}</p>
      <div className="role-activity">
        <span><strong>{currentWorkers}</strong> 当前 Worker</span>
        <span><strong>{taskCount}</strong> 个任务</span>
      </div>
      <dl>
        {role === undefined ? (
          <div><dt>状态</dt><dd>项目未提供 RoleSpec，此岗位不会启动</dd></div>
        ) : (
          <>
            <div><dt>工具</dt><dd>{role.tools.length === 0 ? '未配置' : role.tools.join('、')}</dd></div>
            <div><dt>Skills</dt><dd>{role.skills === undefined || role.skills.length === 0 ? '未配置' : role.skills.map((skill) => skill.id).join('、')}</dd></div>
          </>
        )}
      </dl>
    </article>
  )
}

export function RolesView({ snapshot }: { readonly snapshot: StateSnapshot | null }): ReactNode {
  const roles = snapshot?.roles ?? []
  const roleById = new Map<RoleId, RoleSpec>(roles.map((role) => [role.id, role]))
  const budgetStatus = snapshot?.budget === null || snapshot?.budget === undefined
    ? '当前项目未提供 budget.json'
    : `已用 ${formatCny(snapshot.budget.spentCny)} / ${formatCny(snapshot.budget.limitCny)}`
  return (
    <div className="page roles-view">
      <PageHeader eyebrow="11 个受权限约束的岗位" title="研发团队" description={`${roles.length} / ${ROLE_ROSTER.length} 个岗位已由当前项目提供 RoleSpec；活动数量和任务数量只读取真实状态。`} />
      {snapshot?.source.kind === 'fixture' ? <div className="fixture-notice"><Icon name="warning" size={18} /><span>这是演示快照；未配置岗位不会被标成可用 Agent。</span></div> : null}
      <section className="team-governance" aria-label="成本治理">
        <span className="team-governance-icon"><Icon name="wallet" size={21} /></span>
        <div><strong>成本由预算树和模型路由确定性治理</strong><span>Token 记录用于分析，停工门槛按实际货币成本；不再增设一个会消耗 Token 的“省钱 Agent”。</span></div>
        <small>{budgetStatus}</small>
      </section>
      <div className="role-grid">
        {ROLE_ROSTER.map((entry) => (
          <RoleCard
            key={entry.id}
            entry={entry}
            role={roleById.get(entry.id)}
            currentWorkers={snapshot?.workers.filter((worker) => worker.role === entry.id && ['starting', 'running', 'waiting'].includes(worker.state)).length ?? 0}
            taskCount={snapshot?.packets.filter((packet) => packet.role === entry.id).length ?? 0}
          />
        ))}
      </div>
    </div>
  )
}
