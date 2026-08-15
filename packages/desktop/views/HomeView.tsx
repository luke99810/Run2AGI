import { useEffect, useRef, useState, type ReactNode } from 'react'
import type { DraftAttachment, EngineHandshake, RequirementDraftSnapshot, StateSnapshot } from '../shared/protocol'
import type { CommandDispatcher } from '../renderer/src/command-types'
import { formatCny, hasCapability, PACKET_STATE_LABELS, packetCounts, packetTitle, roleLabel, sameProjectPath } from '../renderer/src/domain'
import type { ResourceOption, TaskContextSelection, TaskHistoryEntry, TaskSession } from '../renderer/src/task-library'
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
  task,
  draftScope,
  taskHistory,
  pluginOptions,
  skillOptions,
  onTaskDraftChange,
  onTaskAttachmentNamesChange,
  onTaskContextChange,
  onTaskSubmitted,
  onOpenExecution,
  onSearchChat,
  onExportChat
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
  readonly task: TaskSession
  readonly draftScope: string
  readonly taskHistory: readonly TaskHistoryEntry[]
  readonly pluginOptions: readonly ResourceOption[]
  readonly skillOptions: readonly ResourceOption[]
  readonly onTaskDraftChange: (text: string) => void
  readonly onTaskAttachmentNamesChange: (names: readonly string[]) => void
  readonly onTaskContextChange: (context: TaskContextSelection) => void
  readonly onTaskSubmitted: () => void
  readonly onOpenExecution: () => void
  readonly onSearchChat: () => void
  readonly onExportChat: () => Promise<boolean>
}): ReactNode {
  const chatMenuRef = useRef<HTMLDetailsElement>(null)
  const [planSubmitting, setPlanSubmitting] = useState(false)
  const [planFeedback, setPlanFeedback] = useState<string | null>(null)
  const [chatActionStatus, setChatActionStatus] = useState<string | null>(null)
  const packets = snapshot?.packets ?? []
  const executablePackets = packets.filter((packet) => packet.state !== 'accepted' && packet.state !== 'abandoned')
  const counts = packetCounts(packets)
  const completedPackets = counts.accepted + counts.abandoned
  const currentStep = packets.length === 0 ? 0 : Math.min(completedPackets + 1, packets.length)
  const currentWorkers = snapshot?.workers.filter((worker) => worker.state === 'running' || worker.state === 'starting' || worker.state === 'waiting') ?? []
  const runningLimit = snapshot?.scheduling?.wipLimits.running
  const bottleneckState = snapshot?.flow?.bottleneck?.state
  const progressSummary = [
    completedPackets === packets.length ? '任务已完成' : currentWorkers.length > 0 ? `${currentWorkers.length} 个 Agent 正在工作` : '等待下一步',
    runningLimit === undefined ? null : `WIP ${counts.running}/${runningLimit}`,
    bottleneckState === undefined ? null : `瓶颈：${PACKET_STATE_LABELS[bottleneckState]}`
  ].filter((item): item is string => item !== null).join(' · ')
  const budget = snapshot?.budget
  const stateDirectoryMissing = snapshot?.warnings.some((warning) => warning.startsWith('[missing] State directory is unavailable:')) ?? false
  const isProject = snapshot?.source.kind === 'project'
  const projectBound = isProject && sameProjectPath(handshake.projectRoot, snapshot?.source.rootPath)
  const requirementAvailable = projectBound && handshake.connected && handshake.runId !== undefined && hasCapability(handshake.capabilities, 'submit_requirement')
  const planAvailable = projectBound && handshake.connected && handshake.runId !== undefined && hasCapability(handshake.capabilities, 'confirm_plan') && executablePackets.length > 0
  const requirementUnavailableReason = !isProject
    ? '请先打开真实项目；演示快照不能发起任务'
    : !handshake.connected
      ? 'A/B 执行引擎尚未连接'
      : handshake.runId === undefined
        ? '引擎未提供权威运行编号'
        : !projectBound
          ? 'A/B 执行引擎未绑定当前项目'
        : '当前引擎未开放需求接收能力'

  useEffect(() => {
    setPlanSubmitting(false)
    setPlanFeedback(null)
  }, [snapshot?.source.id])

  async function startExistingPlan(): Promise<void> {
    setPlanSubmitting(true)
    setPlanFeedback(null)
    try {
      const receipt = await dispatch({
        action: 'confirm_plan',
        agentId: 'control-plane',
        payload: { packetIds: executablePackets.map((packet) => packet.id).sort() }
      })
      setPlanFeedback(receipt.status === 'rejected'
        ? `引擎拒绝执行：${receipt.reason ?? '未提供原因'}`
        : '执行请求已被 A/B 引擎接收，状态将从本地权威文件自动刷新。')
    } catch (error) {
      setPlanFeedback(error instanceof Error ? error.message : String(error))
    } finally {
      setPlanSubmitting(false)
    }
  }

  return (
    <div className="home-view">
      <section className="home-hero" aria-labelledby="new-task-title">
        <div className="task-titlebar">
          <strong>{task.title}</strong>
          <details className="chat-actions" ref={chatMenuRef}>
            <summary aria-label="聊天操作" title="聊天操作">•••</summary>
            <div role="menu">
              <button type="button" role="menuitem" onClick={() => {
                chatMenuRef.current?.removeAttribute('open')
                onSearchChat()
              }}><Icon name="search" size={17} /><span><strong>搜索对话记录</strong><small>消息、命令、回执、文件和证据</small></span></button>
              <button type="button" role="menuitem" onClick={() => {
                chatMenuRef.current?.removeAttribute('open')
                void onExportChat().then((exported) => setChatActionStatus(exported ? '任务记录已导出' : null)).catch((error: unknown) => setChatActionStatus(error instanceof Error ? error.message : String(error)))
              }}><Icon name="file" size={17} /><span><strong>导出对话记录</strong><small>保存需求、附件、命令、回执和运行引用</small></span></button>
            </div>
          </details>
        </div>
        {chatActionStatus === null ? null : <p className="chat-action-status" role="status">{chatActionStatus}</p>}
        <div className="eyebrow">新对话</div>
        <h1 id="new-task-title">要做什么软件？</h1>
        <p>
          {snapshot === null
            ? '尚未选择工作区'
            : `${snapshot.source.label} · ${snapshot.source.kind === 'fixture' ? '演示快照，只读' : handshake.connected ? '引擎已连接' : '状态可读，引擎未连接'}`}
        </p>
        {packets.length === 0 ? null : (
          <details className="conversation-progress">
            <summary>
              <span className={`conversation-progress-wheel${currentWorkers.length > 0 ? ' active' : ''}`} aria-hidden="true" />
              <strong>第 {currentStep} / {packets.length} 步</strong>
              <span>{progressSummary}</span>
              <span className="conversation-progress-more" aria-hidden="true">•••</span>
            </summary>
            <div className="conversation-progress-list">
              {packets.map((packet, index) => (
                <button type="button" key={packet.id} onClick={onOpenExecution}>
                  <span className="conversation-progress-index">{index + 1}</span>
                  <span><strong>{packetTitle(packet)}</strong><small>{roleLabel(packet.role)}</small></span>
                  <em className={`packet-progress-${packet.state}`}>{PACKET_STATE_LABELS[packet.state]}</em>
                </button>
              ))}
            </div>
          </details>
        )}
        <RequirementComposer
          canSubmit={requirementAvailable}
          {...(requirementUnavailableReason === undefined ? {} : { unavailableReason: requirementUnavailableReason })}
          taskId={task.id}
          draftScope={draftScope}
          legacyScope={snapshot?.source.id ?? 'unassigned'}
          taskContext={task.context}
          taskHistory={taskHistory}
          pluginOptions={pluginOptions}
          skillOptions={skillOptions}
          onDraftChange={onTaskDraftChange}
          onAttachmentNamesChange={onTaskAttachmentNamesChange}
          onContextChange={onTaskContextChange}
          onSubmitted={onTaskSubmitted}
          dispatch={dispatch}
          selectDraftFiles={selectDraftFiles}
          selectDraftFolders={selectDraftFolders}
          loadRequirementDraft={loadRequirementDraft}
          saveRequirementDraft={saveRequirementDraft}
          moveRequirementDraft={moveRequirementDraft}
          discardDraftAttachment={discardDraftAttachment}
        />
      </section>

      {isProject && executablePackets.length > 0 ? (
        <section className="plan-launch-band" aria-label="执行已有计划">
          <div>
            <span>已落盘计划</span>
            <strong>{executablePackets.length} 个非终态 WorkPacket</strong>
            <small>{planAvailable
              ? '由 A 控制平面调度，B WorkerRuntime 在隔离 worktree 中执行。'
              : (handshake.unavailableReason ?? '当前引擎未开放已有计划执行能力。')}</small>
          </div>
          <button
            type="button"
            className="primary-button plan-launch-button"
            disabled={!planAvailable || planSubmitting}
            onClick={() => { void startExistingPlan() }}
          >
            <Icon name="pulse" size={18} />
            {planSubmitting ? '正在提交' : '开始执行已有计划'}
          </button>
          {planFeedback === null ? null : <p className="plan-launch-feedback" role="status">{planFeedback}</p>}
        </section>
      ) : null}

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
            <button type="button" className="overview-card" onClick={onOpenExecution}>
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
