import type { ReactNode } from 'react'
import type { RoleId, RoleSpec } from '@codentum/contracts'
import type { StateSnapshot } from '../shared/protocol'
import { ROLE_ROSTER, roleLabel, type RoleRosterEntry } from '../renderer/src/domain'
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
          : <span className="model-badge unconfigured">待项目投影</span>}
      </header>
      <p>{role?.summary ?? entry.summary}</p>
      <div className="role-activity">
        <span><strong>{currentWorkers}</strong> 当前 Worker</span>
        <span><strong>{taskCount}</strong> 个任务</span>
      </div>
      <dl>
        {role === undefined ? (
          <div><dt>状态</dt><dd>系统岗位已定义；当前项目尚未提供 RoleSpec 投影，因此不会启动 Worker</dd></div>
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
  return (
    <div className="page roles-view">
      <PageHeader eyebrow={`系统岗位 ${ROLE_ROSTER.length} · 项目投影 ${roles.length}`} title="研发团队" description="岗位名称来自团队 RoleSpec 清单；模型、工具、Skills、Worker 和任务数量只读取当前项目与 A/B 运行时的真实投影。" />
      {snapshot?.source.kind === 'fixture' ? <div className="fixture-notice"><Icon name="warning" size={18} /><span>这是演示状态；项目未投影的岗位不会被标成可运行 Agent。</span></div> : null}
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
