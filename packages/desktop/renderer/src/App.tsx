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
import { ConversationsView, HelpView, ResourceLibraryView, SettingsView } from '../../views/WorkbenchViews'
import {
  createTaskSession,
  historyForAgent,
  loadTaskSessions,
  loadWorkbenchPreferences,
  saveTaskSessions,
  saveWorkbenchPreferences,
  taskDraftScope,
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

function warningCopy(warning: string): string {
  if (warning.startsWith('[missing] State directory is unavailable:')) {
    return '工作区已正确打开；尚未生成 .codentum 运行状态。当前可编辑需求和添加附件，正式执行需引擎完成首次项目初始化。'
  }
  return warning
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

  const exportActiveChat = useCallback(async (): Promise<boolean> => {
    if (activeTask === undefined) return false
    const draft = await desktop.loadRequirementDraft(taskDraftScope(activeTask))
    const attachmentLines = draft.attachments.length === 0
      ? ['- 无']
      : draft.attachments.map((attachment) => `- ${attachment.name} (${attachment.kind === 'folder' ? `${attachment.fileCount} 个文件` : `${attachment.sizeBytes} bytes`}, SHA-256 ${attachment.sha256})`)
    const context = activeTask.context
    const markdown = [
      `# ${activeTask.title}`,
      '',
      `- Task ID: ${activeTask.id}`,
      `- 项目来源: ${activeTask.sourceId}`,
      `- 状态: ${activeTask.status === 'submitted' ? '已提交' : '草稿'}`,
      `- 访问权限: ${context.accessMode}`,
      `- 创建时间: ${activeTask.createdAt}`,
      `- 更新时间: ${activeTask.updatedAt}`,
      '',
      '## 需求记录',
      '',
      draft.text.trim() || '（尚未输入）',
      '',
      '## 引用文件',
      '',
      ...attachmentLines,
      ''
    ].join('\n')
    return desktop.exportTaskRecord(activeTask.title, markdown)
  }, [activeTask, desktop])

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
    return desktop.sendCommand(createOperatorCommand({
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
    }))
  }, [desktop])

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
          <RolesView snapshot={desktop.snapshot} />
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
      view = <DeliveryView snapshot={desktop.snapshot} enabled={validationEnabled} />
      break
    case 'conversations':
      view = <ConversationsView tasks={sourceTasks} activeTaskId={activeTask?.id ?? null} onSelectTask={selectTask} onNewTask={createNewTask} />
      break
    case 'plugins':
    case 'knowledge':
    case 'skills':
      view = <ResourceLibraryView kind={navigation} task={activeTask} onContextChange={updateActiveContext} />
      break
    case 'settings':
      view = <SettingsView preferences={preferences} onChange={setPreferences} />
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
          {visibleWarnings.map((warning, index) => <div className="global-warning" key={`${warning}-${index}`}><WarningNotice message={warningCopy(warning)} /></div>)}
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
