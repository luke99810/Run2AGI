import { useCallback, useState, type ReactNode } from 'react'
import type { OperatorAction } from '../../shared/protocol'
import type { CommandDispatcher, CommandRequest } from './command-types'
import { createOperatorCommand, hasCapability, sameProjectPath, type NavigationKey } from './domain'
import { useDesktop } from './useDesktop'
import { Sidebar } from '../../panels/Sidebar'
import { Topbar } from '../../panels/Topbar'
import { ErrorNotice, WarningNotice } from '../../panels/Common'
import { HomeView } from '../../views/HomeView'
import { ExecutionView } from '../../views/ExecutionView'
import { BoardView } from '../../views/BoardView'
import { WavesView } from '../../views/WavesView'
import { DependencyView } from '../../views/DependencyView'
import { CostView } from '../../views/CostView'
import { RolesView } from '../../views/RolesView'
import { DeliveryView } from '../../views/DeliveryView'

let fallbackCommandCounter = 0

function warningCopy(warning: string): string {
  if (warning.startsWith('[missing] State directory is unavailable:')) {
    return '此项目尚未包含 .codentum 状态目录，当前以只读空项目打开。'
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
  const [focusedWorkerId, setFocusedWorkerId] = useState<string | null>(null)

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
      view = (
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
          onOpenExecution={() => setNavigation('execution')}
          onOpenBoard={() => setNavigation('board')}
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
    case 'board':
      view = <BoardView snapshot={desktop.snapshot} />
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
    case 'roles':
      view = <RolesView snapshot={desktop.snapshot} />
      break
    case 'delivery':
      view = <DeliveryView snapshot={desktop.snapshot} />
      break
  }

  return (
    <div className={`app-shell${sidebarCollapsed ? ' sidebar-is-collapsed' : ''}`}>
      <Sidebar active={navigation} snapshot={desktop.snapshot} onNavigate={setNavigation} onSelectWorker={openWorker} collapsed={sidebarCollapsed} onToggle={() => setSidebarCollapsed((current) => !current)} />
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
          {desktop.snapshot?.warnings.map((warning, index) => <div className="global-warning" key={`${warning}-${index}`}><WarningNotice message={warningCopy(warning)} /></div>)}
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
