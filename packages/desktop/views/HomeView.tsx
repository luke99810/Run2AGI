import type { ReactNode } from 'react'
import type { DraftAttachment, EngineHandshake, RequirementDraftSnapshot, StateSnapshot } from '../shared/protocol'
import type { CommandDispatcher } from '../renderer/src/command-types'
import { formatCny, hasCapability, packetCounts, roleLabel, sameProjectPath } from '../renderer/src/domain'
import { RequirementComposer } from '../inputs/RequirementComposer'
import { EmptyState, Icon } from '../panels/Common'

export function HomeView({
  snapshot,
  handshake,
  dispatch,
  selectDraftFiles,
  selectDraftFolders,
  loadRequirementDraft,
  saveRequirementDraft,
  moveRequirementDraft,
  discardDraftAttachment,
  onOpenExecution,
  onOpenBoard
}: {
  readonly snapshot: StateSnapshot | null
  readonly handshake: EngineHandshake
  readonly dispatch: CommandDispatcher
  readonly selectDraftFiles: (scopeId: string) => Promise<RequirementDraftSnapshot>
  readonly selectDraftFolders: (scopeId: string) => Promise<RequirementDraftSnapshot>
  readonly loadRequirementDraft: (scopeId: string) => Promise<RequirementDraftSnapshot>
  readonly saveRequirementDraft: (scopeId: string, draft: RequirementDraftSnapshot) => Promise<void>
  readonly moveRequirementDraft: (sourceScopeId: string, targetScopeId: string) => Promise<RequirementDraftSnapshot>
  readonly discardDraftAttachment: (scopeId: string, attachmentId: DraftAttachment['id']) => Promise<RequirementDraftSnapshot>
  readonly onOpenExecution: () => void
  readonly onOpenBoard: () => void
}): ReactNode {
  const packets = snapshot?.packets ?? []
  const counts = packetCounts(packets)
  const currentWorkers = snapshot?.workers.filter((worker) => worker.state === 'running' || worker.state === 'starting' || worker.state === 'waiting') ?? []
  const budget = snapshot?.budget
  const stateDirectoryMissing = snapshot?.warnings.some((warning) => warning.startsWith('[missing] State directory is unavailable:')) ?? false
  const isProject = snapshot?.source.kind === 'project'
  const projectBound = isProject && sameProjectPath(handshake.projectRoot, snapshot?.source.rootPath)
  const requirementAvailable = projectBound && handshake.connected && handshake.runId !== undefined && hasCapability(handshake.capabilities, 'submit_requirement')
  const requirementUnavailableReason = !isProject
    ? '请先打开真实项目；演示快照不能发起任务'
    : !handshake.connected
      ? 'A/B 执行引擎尚未连接'
      : handshake.runId === undefined
        ? '引擎未提供权威运行编号'
        : !projectBound
          ? 'A/B 执行引擎未绑定当前项目'
        : '当前引擎未开放需求接收能力'

  return (
    <div className="home-view">
      <section className="home-hero" aria-labelledby="new-task-title">
        <div className="eyebrow">老板工作台</div>
        <h1 id="new-task-title">交代研发任务</h1>
        <p>
          {snapshot === null
            ? '尚未选择工作区'
            : `${snapshot.source.label} · ${snapshot.source.kind === 'fixture' ? '演示快照，只读' : handshake.connected ? '引擎已连接' : '状态可读，引擎未连接'}`}
        </p>
        <RequirementComposer
          canSubmit={requirementAvailable}
          canAddFiles={snapshot !== null}
          {...(requirementUnavailableReason === undefined ? {} : { unavailableReason: requirementUnavailableReason })}
          sourceId={snapshot?.source.id ?? null}
          dispatch={dispatch}
          selectDraftFiles={selectDraftFiles}
          selectDraftFolders={selectDraftFolders}
          loadRequirementDraft={loadRequirementDraft}
          saveRequirementDraft={saveRequirementDraft}
          moveRequirementDraft={moveRequirementDraft}
          discardDraftAttachment={discardDraftAttachment}
        />
      </section>

      <section className="home-overview" aria-label="项目概览">
        <div className="section-title-row">
          <div><span>当前工作区</span><h2>{snapshot?.source.label ?? '还没有打开项目'}</h2></div>
          {snapshot?.source.kind === 'fixture' ? <span className="fixture-badge large">演示快照 · 不代表真实执行</span> : null}
        </div>
        {snapshot === null ? (
          <EmptyState title="等待项目状态" detail="从顶部打开任意本地项目；没有 .codentum 时会显示为尚未初始化。" icon="folder" />
        ) : packets.length === 0 ? (
          <EmptyState
            title={stateDirectoryMissing ? '项目尚未初始化 Codentum' : '这个项目还没有任务'}
            detail={stateDirectoryMissing
              ? '目录已打开；A/B 引擎写入 .codentum 后，任务与 Worker 状态会自动出现。'
              : '状态源读取成功。需求能力接通后，可从上方发起第一项工作。'}
            icon={stateDirectoryMissing ? 'folder' : 'plus'}
          />
        ) : (
          <div className="overview-grid">
            <button type="button" className="overview-card" onClick={onOpenExecution}>
              <span className="overview-icon mint"><Icon name="pulse" size={22} /></span>
              <div><strong>{currentWorkers.length}</strong><span>当前 Worker</span></div>
              <small>{currentWorkers.length === 0 ? '没有当前 Worker 投影' : currentWorkers.map((worker) => roleLabel(worker.role)).join('、')}</small>
              <Icon name="chevron" size={18} />
            </button>
            <button type="button" className="overview-card" onClick={onOpenBoard}>
              <span className="overview-icon blue"><Icon name="board" size={22} /></span>
              <div><strong>{packets.length}</strong><span>任务总数</span></div>
              <small>{counts.running} 执行中 · {counts.blocked} 受阻 · {counts.accepted} 已通过</small>
              <Icon name="chevron" size={18} />
            </button>
            <div className="overview-card static">
              <span className="overview-icon amber"><Icon name="wallet" size={22} /></span>
              <div><strong>{budget === null || budget === undefined ? '—' : formatCny(budget.spentCny)}</strong><span>已发生成本</span></div>
              <small>{budget === null || budget === undefined ? '项目尚未提供预算文件' : `总预算 ${formatCny(budget.limitCny)}`}</small>
            </div>
          </div>
        )}
      </section>
    </div>
  )
}
