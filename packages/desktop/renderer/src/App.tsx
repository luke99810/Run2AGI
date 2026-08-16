import { useCallback, useEffect, useState, type CSSProperties, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react'
import type { OperatorAction } from '../../shared/protocol'
import type { CommandDispatcher, CommandRequest } from './command-types'
import { createOperatorCommand, hasCapability, sameProjectPath, warningsForDisplay, type NavigationKey } from './domain'
import { useDesktop } from './useDesktop'
import { Sidebar } from '../../panels/Sidebar'
import { Topbar } from '../../panels/Topbar'
import { ErrorNotice, WarningNotice } from '../../panels/Common'
import { HomeView } from '../../views/HomeView'
import { ExecutionView } from '../../views/ExecutionView'
import { WavesView } from '../../views/WavesView'
import { DependencyView } from '../../views/DependencyView'
import { CostView } from '../../views/CostView'
import { RolesView } from '../../views/RolesView'
import { DeliveryView } from '../../views/DeliveryView'
import { EvidenceView } from '../../views/EvidenceView'
import { McpView } from '../../views/McpView'
import { ConnectorsView } from '../../views/ConnectorsView'
import { ConversationsView, HelpView, ResourceLibraryView, SettingsView } from '../../views/WorkbenchViews'
import {
  appendTaskConversation,
  createTaskSession,
  historyForAgent,
  loadTaskSessions,
  loadWorkbenchPreferences,
  pluginOptionsFromMcp,
  saveTaskSessions,
  saveWorkbenchPreferences,
  skillOptionsFromRoles,
  taskDraftScope,
  taskConversationEntries,
  taskRequestsValidation,
  updateTaskFromDraft,
  type TaskContextSelection,
  type TaskSession,
  type WorkbenchPreferences
} from './task-library'

let fallbackCommandCounter = 0
const SIDEBAR_WIDTH_KEY = 'codentum.sidebar.width.v2'
const MIN_SIDEBAR_WIDTH = 200
const MAX_SIDEBAR_WIDTH = 380

function clampSidebarWidth(width: number): number {
  const viewportMaximum = typeof window === 'undefined' ? MAX_SIDEBAR_WIDTH : Math.max(MIN_SIDEBAR_WIDTH, window.innerWidth - 480)
  return Math.round(Math.min(Math.min(MAX_SIDEBAR_WIDTH, viewportMaximum), Math.max(MIN_SIDEBAR_WIDTH, width)))
}

function loadSidebarWidth(): number {
  try {
    const stored = Number.parseInt(localStorage.getItem(SIDEBAR_WIDTH_KEY) ?? '', 10)
    return Number.isFinite(stored) ? clampSidebarWidth(stored) : 248
  } catch {
    return 248
  }
}

function warningCopy(warning: string, engineReason?: string): string {
  if (warning.startsWith('[missing] State directory is unavailable:')) {
    return engineReason === undefined
      ? '工作区已正确打开，A/B 引擎正在完成首次项目初始化；初始化后会自动显示 Agent、Skills、MCP 和 Worker 状态。'
      : `工作区已正确打开，但 A/B 引擎尚未完成首次项目初始化：${engineReason}`
  }
  return warning
}

function conversationKindLabel(kind: 'user' | 'command' | 'receipt' | 'agent' | 'evidence' | 'error'): string {
  return {
    user: '用户消息',
    command: 'C 命令',
    receipt: '引擎回执',
    agent: 'Agent / Worker 事件',
    evidence: '证据引用',
    error: '错误'
  }[kind]
}

function nextCommandId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  fallbackCommandCounter += 1
  return `cmd-${Date.now()}-${fallbackCommandCounter}`
}

export function App(): ReactNode {
  const desktop = useDesktop()
  const [navigation, setNavigation] = useState<NavigationKey>('home')
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [sidebarWidth, setSidebarWidth] = useState(loadSidebarWidth)
  const [sidebarResizing, setSidebarResizing] = useState(false)
  const [focusedWorkerId, setFocusedWorkerId] = useState<string | null>(null)
  const [tasks, setTasks] = useState<readonly TaskSession[]>(loadTaskSessions)
  const [preferences, setPreferences] = useState<WorkbenchPreferences>(loadWorkbenchPreferences)
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null)
  const currentSourceId = desktop.selectedSourceId ?? 'unassigned'
  const sourceTasks = tasks
    .filter((task) => task.sourceId === currentSourceId)
    .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt))
  const activeTask = sourceTasks.find((task) => task.id === activeTaskId)
  const validationEnabled = activeTask !== undefined && taskRequestsValidation(activeTask)
  const visibleWarnings = warningsForDisplay(desktop.snapshot?.warnings ?? [])
  const pluginOptions = pluginOptionsFromMcp(desktop.snapshot?.mcpServices)
  const skillOptions = skillOptionsFromRoles(desktop.snapshot?.roles, desktop.snapshot?.skills)

  useEffect(() => {
    saveTaskSessions(tasks)
  }, [tasks])

  useEffect(() => {
    saveWorkbenchPreferences(preferences)
  }, [preferences])

  useEffect(() => {
    if (navigation === 'delivery' && !validationEnabled) setNavigation('home')
  }, [navigation, validationEnabled])

  useEffect(() => {
    try {
      localStorage.setItem(SIDEBAR_WIDTH_KEY, String(sidebarWidth))
    } catch {
      // The current width still works for this session when storage is unavailable.
    }
  }, [sidebarWidth])

  const resizeSidebar = useCallback((clientX: number): void => {
    setSidebarWidth(clampSidebarWidth(clientX))
  }, [])

  const startSidebarResize = useCallback((event: ReactPointerEvent<HTMLDivElement>): void => {
    if (sidebarCollapsed || event.button !== 0) return
    event.currentTarget.setPointerCapture(event.pointerId)
    setSidebarResizing(true)
    resizeSidebar(event.clientX)
  }, [resizeSidebar, sidebarCollapsed])

  const moveSidebarResize = useCallback((event: ReactPointerEvent<HTMLDivElement>): void => {
    if (!sidebarResizing || !event.currentTarget.hasPointerCapture(event.pointerId)) return
    resizeSidebar(event.clientX)
  }, [resizeSidebar, sidebarResizing])

  const stopSidebarResize = useCallback((event: ReactPointerEvent<HTMLDivElement>): void => {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
    setSidebarResizing(false)
  }, [])

  const resizeSidebarWithKeyboard = useCallback((event: ReactKeyboardEvent<HTMLDivElement>): void => {
    const next = event.key === 'ArrowLeft' ? sidebarWidth - 8
      : event.key === 'ArrowRight' ? sidebarWidth + 8
        : event.key === 'Home' ? MIN_SIDEBAR_WIDTH
          : event.key === 'End' ? MAX_SIDEBAR_WIDTH
            : null
    if (next === null) return
    event.preventDefault()
    setSidebarWidth(clampSidebarWidth(next))
  }, [sidebarWidth])

  useEffect(() => {
    if (activeTask !== undefined) return
    const existing = sourceTasks[0]
    if (existing !== undefined) {
      setActiveTaskId(existing.id)
      return
    }
    const created = createTaskSession(currentSourceId, preferences)
    setTasks((current) => [...current, created])
    setActiveTaskId(created.id)
  }, [activeTask, currentSourceId, preferences, sourceTasks])

  const updateTask = useCallback((taskId: string, updater: (task: TaskSession) => TaskSession): void => {
    setTasks((current) => current.map((task) => task.id === taskId ? updater(task) : task))
  }, [])

  const createNewTask = useCallback((): void => {
    const created = createTaskSession(currentSourceId, preferences)
    setTasks((current) => [...current, created])
    setActiveTaskId(created.id)
    setNavigation('home')
  }, [currentSourceId, preferences])

  const selectTask = useCallback((taskId: string): void => {
    setActiveTaskId(taskId)
    setNavigation('home')
  }, [])

  const updateActiveContext = useCallback((context: TaskContextSelection): void => {
    if (activeTaskId === null) return
    updateTask(activeTaskId, (task) => ({ ...task, context, updatedAt: new Date().toISOString() }))
  }, [activeTaskId, updateTask])

  const exportTaskChat = useCallback(async (task: TaskSession): Promise<boolean> => {
    const draft = await desktop.loadRequirementDraft(taskDraftScope(task))
    const attachmentLines = draft.attachments.length === 0
      ? ['- 无']
      : draft.attachments.map((attachment) => `- ${attachment.name} (${attachment.kind === 'folder' ? `${attachment.fileCount} 个文件` : `${attachment.sizeBytes} bytes`}, SHA-256 ${attachment.sha256})`)
    const context = task.context
    const timeline = taskConversationEntries(task, desktop.snapshot)
    const timelineLines = timeline.length === 0
      ? ['- 当前没有已记录的消息、命令回执、Agent 事件或证据引用。']
      : timeline.flatMap((entry) => {
        const metadata = [entry.action, entry.commandId, entry.packetId, ...(entry.evidenceRefs ?? [])].filter(Boolean).join(' · ')
        return [`### ${new Date(entry.at).toLocaleString('zh-CN')} · ${conversationKindLabel(entry.kind)}`, '', entry.text, ...(metadata === '' ? [] : ['', `> ${metadata}`]), '']
      })
    const markdown = [
      `# ${task.title}`,
      '',
      `- Task ID: ${task.id}`,
      `- 项目来源: ${task.sourceId}`,
      `- 状态: ${task.status === 'submitted' ? '已提交' : '草稿'}`,
      `- 访问权限: ${context.accessMode}`,
      `- 创建时间: ${task.createdAt}`,
      `- 更新时间: ${task.updatedAt}`,
      '',
      '## 需求记录',
      '',
      draft.text.trim() || '（尚未输入）',
      '',
      '## 引用文件',
      '',
      ...attachmentLines,
      '',
      '## 对话、命令与运行记录',
      '',
      ...timelineLines,
      ''
    ].join('\n')
    return desktop.exportTaskRecord(task.title, markdown)
  }, [desktop])

  const exportActiveChat = useCallback(async (): Promise<boolean> => {
    if (activeTask === undefined) return false
    return exportTaskChat(activeTask)
  }, [activeTask, exportTaskChat])

  const openWorker = useCallback((workerId: string): void => {
    setFocusedWorkerId(workerId)
    setNavigation('execution')
  }, [])

  const dispatch: CommandDispatcher = useCallback(async (request: CommandRequest) => {
    const snapshot = desktop.snapshot
    if (snapshot === null) throw new Error('请先打开一个项目或状态快照')
    if (snapshot.source.kind !== 'project') throw new Error('演示快照只读，不能发送执行命令')
    if (!desktop.handshake.connected) throw new Error(desktop.handshake.unavailableReason ?? '本地引擎未连接')
    if (desktop.handshake.runId === undefined) throw new Error('引擎未提供权威运行编号')
    const projectRoot = snapshot.source.rootPath
    if (projectRoot === undefined || !sameProjectPath(desktop.handshake.projectRoot, projectRoot)) {
      throw new Error('本地引擎绑定的项目与当前工作区不一致')
    }
    if (!hasCapability(desktop.handshake.capabilities, request.action)) {
      throw new Error(`引擎未开放此操作能力：${request.action}`)
    }
    if (desktop.selectedSourceId !== snapshot.source.id) throw new Error('状态来源正在切换，请刷新后重试')
    const command = createOperatorCommand({
      commandId: nextCommandId(),
      runId: desktop.handshake.runId,
      expectedRevision: desktop.handshake.stateRevision,
      agentId: request.agentId,
      ...(request.packetId === undefined ? {} : { packetId: request.packetId }),
      ...(request.moduleId === undefined ? {} : { moduleId: request.moduleId }),
      action: request.action as OperatorAction,
      payload: {
        ...(request.payload ?? {}),
        projectRoot
      },
      requestedAt: new Date().toISOString()
    })
    const payloadTaskId = request.payload?.['taskId']
    const taskId = typeof payloadTaskId === 'string' ? payloadTaskId : activeTaskId
    if (taskId !== null) {
      if (request.action === 'submit_requirement' && typeof request.payload?.['requirement'] === 'string') {
        updateTask(taskId, (task) => appendTaskConversation(task, {
          id: `${command.commandId}:user`,
          kind: 'user',
          at: command.requestedAt,
          text: request.payload?.['requirement'] as string,
          commandId: command.commandId,
          action: request.action
        }))
      }
      updateTask(taskId, (task) => appendTaskConversation(task, {
        id: `${command.commandId}:command`,
        kind: 'command',
        at: command.requestedAt,
        text: `向 ${request.agentId} 发送 ${request.action}`,
        commandId: command.commandId,
        action: request.action,
        ...(request.packetId === undefined ? {} : { packetId: request.packetId })
      }))
    }
    try {
      const receipt = await desktop.sendCommand(command)
      if (taskId !== null) updateTask(taskId, (task) => appendTaskConversation(task, {
        id: `${command.commandId}:receipt`,
        kind: 'receipt',
        at: receipt.receivedAt,
        text: receipt.reason === undefined ? `引擎回执：${receipt.status}` : `引擎回执：${receipt.status} · ${receipt.reason}`,
        commandId: receipt.commandId,
        action: request.action,
        ...(request.packetId === undefined ? {} : { packetId: request.packetId })
      }))
      return receipt
    } catch (error) {
      if (taskId !== null) updateTask(taskId, (task) => appendTaskConversation(task, {
        id: `${command.commandId}:error`,
        kind: 'error',
        at: new Date().toISOString(),
        text: error instanceof Error ? error.message : String(error),
        commandId: command.commandId,
        action: request.action,
        ...(request.packetId === undefined ? {} : { packetId: request.packetId })
      }))
      throw error
    }
  }, [activeTaskId, desktop, updateTask])

  let view: ReactNode
  switch (navigation) {
    case 'home':
      view = activeTask === undefined ? (
        <div className="task-preparing" role="status">正在准备独立任务空间…</div>
      ) : (
        <HomeView
          snapshot={desktop.snapshot}
          handshake={desktop.handshake}
          dispatch={dispatch}
          selectDraftFiles={desktop.selectDraftFiles}
          selectDraftFolders={desktop.selectDraftFolders}
          loadRequirementDraft={desktop.loadRequirementDraft}
          saveRequirementDraft={desktop.saveRequirementDraft}
          moveRequirementDraft={desktop.moveRequirementDraft}
          discardDraftAttachment={desktop.discardDraftAttachment}
          task={activeTask}
          draftScope={taskDraftScope(activeTask)}
          taskHistory={historyForAgent(sourceTasks, activeTask.id)}
          pluginOptions={pluginOptions}
          skillOptions={skillOptions}
          onTaskDraftChange={(text) => updateTask(activeTask.id, (task) => updateTaskFromDraft(task, text))}
          onTaskAttachmentNamesChange={(attachmentNames) => updateTask(activeTask.id, (task) => ({ ...task, attachmentNames, updatedAt: new Date().toISOString() }))}
          onTaskContextChange={(context: TaskContextSelection) => updateTask(activeTask.id, (task) => ({ ...task, context, updatedAt: new Date().toISOString() }))}
          onTaskSubmitted={() => updateTask(activeTask.id, (task) => ({ ...task, status: 'submitted', updatedAt: new Date().toISOString() }))}
          onOpenExecution={() => setNavigation('execution')}
          onSearchChat={() => setNavigation('conversations')}
          onExportChat={exportActiveChat}
        />
      )
      break
    case 'execution':
      view = (
        <ExecutionView
          snapshot={desktop.snapshot}
          handshake={desktop.handshake}
          dispatch={dispatch}
          focusedWorkerId={focusedWorkerId}
          onFocusHandled={() => setFocusedWorkerId(null)}
        />
      )
      break
    case 'waves':
      view = <WavesView snapshot={desktop.snapshot} />
      break
    case 'dependency':
      view = <DependencyView snapshot={desktop.snapshot} />
      break
    case 'cost':
      view = <CostView snapshot={desktop.snapshot} />
      break
    case 'evidence':
      view = <EvidenceView snapshot={desktop.snapshot} />
      break
    case 'roles':
      view = (
        <div className="team-combined-view">
          <RolesView snapshot={desktop.snapshot} listConfigurations={desktop.listAgentConfigurations} saveConfiguration={desktop.saveAgentConfiguration} removeConfiguration={desktop.removeAgentConfiguration} selectSystemDocument={desktop.selectAgentSystemDocument} clearSystemDocument={desktop.clearAgentSystemDocument} />
          <ExecutionView
            snapshot={desktop.snapshot}
            handshake={desktop.handshake}
            dispatch={dispatch}
            focusedWorkerId={focusedWorkerId}
            onFocusHandled={() => setFocusedWorkerId(null)}
            embedded
          />
        </div>
      )
      break
    case 'delivery':
      view = <DeliveryView snapshot={desktop.snapshot} sourceId={desktop.selectedSourceId} packageArtifact={desktop.packageProjectArtifact} enabled={validationEnabled} />
      break
    case 'conversations':
      view = <ConversationsView tasks={sourceTasks} snapshot={desktop.snapshot} activeTaskId={activeTask?.id ?? null} onSelectTask={selectTask} onNewTask={createNewTask} onExportTask={exportTaskChat} />
      break
    case 'plugins':
      view = <ConnectorsView services={desktop.snapshot?.mcpServices ?? []} list={desktop.listConnectors} save={desktop.saveConnector} remove={desktop.removeConnector} />
      break
    case 'knowledge':
    case 'skills':
      view = (
        <ResourceLibraryView
          kind={navigation}
          task={activeTask}
          pluginOptions={pluginOptions}
          skillOptions={skillOptions}
          snapshot={desktop.snapshot}
          onContextChange={updateActiveContext}
          listManagedResources={desktop.listManagedResources}
          selectManagedResources={desktop.selectManagedResources}
          addManagedResourceUrl={desktop.addManagedResourceUrl}
          updateManagedResource={desktop.updateManagedResource}
          removeManagedResource={desktop.removeManagedResource}
        />
      )
      break
    case 'mcp':
      view = <McpView services={desktop.snapshot?.mcpServices ?? []} listConfigurations={desktop.listMcpConfigurations} saveConfiguration={desktop.saveMcpConfiguration} removeConfiguration={desktop.removeMcpConfiguration} />
      break
    case 'settings':
      view = <SettingsView preferences={preferences} onChange={setPreferences} onOpenMcp={() => setNavigation('mcp')} getCloudSkillsCatalog={desktop.getCloudSkillsCatalog} setCloudSkillsCatalog={desktop.setCloudSkillsCatalog} />
      break
    case 'help':
      view = <HelpView snapshot={desktop.snapshot} onOpenAgents={() => setNavigation('roles')} />
      break
  }

  return (
    <div
      className={`app-shell${sidebarCollapsed ? ' sidebar-is-collapsed' : ''}${sidebarResizing ? ' sidebar-is-resizing' : ''}`}
      style={{ '--sidebar-width': `${sidebarWidth}px` } as CSSProperties}
    >
      <Sidebar
        active={navigation}
        snapshot={desktop.snapshot}
        tasks={sourceTasks}
        activeTaskId={activeTask?.id ?? null}
        validationEnabled={validationEnabled}
        onNavigate={setNavigation}
        onSelectWorker={openWorker}
        onNewTask={createNewTask}
        onSelectTask={selectTask}
        collapsed={sidebarCollapsed}
        onToggle={() => setSidebarCollapsed((current) => !current)}
      />
      <div
        className="sidebar-resizer"
        role="separator"
        aria-label="调整侧栏宽度"
        aria-orientation="vertical"
        aria-valuemin={MIN_SIDEBAR_WIDTH}
        aria-valuemax={MAX_SIDEBAR_WIDTH}
        aria-valuenow={sidebarWidth}
        tabIndex={sidebarCollapsed ? -1 : 0}
        onPointerDown={startSidebarResize}
        onPointerMove={moveSidebarResize}
        onPointerUp={stopSidebarResize}
        onPointerCancel={stopSidebarResize}
        onKeyDown={resizeSidebarWithKeyboard}
      />
      <div className="app-workspace">
        <Topbar
          sources={desktop.sources}
          selectedSourceId={desktop.selectedSourceId}
          snapshot={desktop.snapshot}
          handshake={desktop.handshake}
          loading={desktop.loading}
          onSelectSource={desktop.selectSource}
          onSelectProject={desktop.selectProject}
          onRefresh={desktop.refresh}
        />
        <div className="content-scroll">
          {desktop.error === null ? null : <div className="global-error"><ErrorNotice message={desktop.error} /></div>}
          {visibleWarnings.map((warning, index) => <div className="global-warning" key={`${warning}-${index}`}><WarningNotice message={warningCopy(warning, desktop.handshake.connected ? undefined : desktop.handshake.unavailableReason)} /></div>)}
          {desktop.loading ? <div className="loading-line" aria-label="正在读取状态"><span /></div> : null}
          {view}
          <footer className="content-footer">
            <span>只读状态：{desktop.snapshot?.revision ?? '—'}</span>
            <span>读取时间：{desktop.snapshot === null ? '—' : new Date(desktop.snapshot.readAt).toLocaleString('zh-CN')}</span>
          </footer>
        </div>
      </div>
    </div>
  )
}
