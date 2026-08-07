import { useEffect, useState, type ReactNode } from 'react'
import type { CommandReceipt, EngineHandshake, OperatorAction, WorkerProjection } from '../shared/protocol'
import type { CommandDispatcher } from '../renderer/src/command-types'
import { hasCapability, projectWorkerModules, roleLabel, shortPacketId } from '../renderer/src/domain'
import { InlineCommandComposer } from '../inputs/InlineCommandComposer'
import { Icon, type IconName } from './Common'

interface ActionDefinition {
  readonly action: OperatorAction
  readonly label: string
  readonly icon: IconName
  readonly tone?: 'danger'
  readonly workerStates?: readonly WorkerProjection['state'][]
  readonly requiresConfirmation?: boolean
}

const DIRECT_ACTIONS: readonly ActionDefinition[] = [
  { action: 'pause_at_safe_point', label: '到安全点暂停', icon: 'pause', workerStates: ['starting', 'running'] },
  { action: 'resume', label: '继续执行', icon: 'pulse', workerStates: ['waiting'] },
  { action: 'stop_keep_memory', label: '停止并保留记忆', icon: 'package', tone: 'danger', workerStates: ['starting', 'running', 'waiting'], requiresConfirmation: true },
  { action: 'stop', label: '停止任务', icon: 'stop', tone: 'danger', workerStates: ['starting', 'running', 'waiting'], requiresConfirmation: true }
] as const

function latestWorkerStatus(worker: WorkerProjection): string | undefined {
  for (const event of [...worker.events].sort((left, right) => right.seq - left.seq)) {
    for (const key of ['state', 'status'] as const) {
      const value = event.payload[key]
      if (typeof value === 'string' && value.trim() !== '') return value.toLowerCase()
    }
  }
  return undefined
}

function receiptCopy(receipt: CommandReceipt): string {
  if (receipt.status === 'applied') return '引擎确认已应用。'
  if (receipt.status === 'waiting_safe_point') return '已受理，等待安全点；任务尚未暂停。'
  if (receipt.status === 'accepted') return '已受理，等待权威状态更新。'
  return `已拒绝${receipt.reason === undefined ? '' : `：${receipt.reason}`}`
}

export function CommandPanel({ worker, selectedModuleId, handshake, commandsAllowed, dispatch, onClose }: {
  readonly worker: WorkerProjection
  readonly selectedModuleId: string | null
  readonly handshake: EngineHandshake
  readonly commandsAllowed: boolean
  readonly dispatch: CommandDispatcher
  readonly onClose: () => void
}): ReactNode {
  const [pendingAction, setPendingAction] = useState<OperatorAction | null>(null)
  const [confirmingAction, setConfirmingAction] = useState<OperatorAction | null>(null)
  const [receipt, setReceipt] = useState<CommandReceipt | null>(null)
  const [error, setError] = useState<string | null>(null)
  const modules = projectWorkerModules(worker)
  const selectedModule = modules.find((module) => module.id === selectedModuleId)
  const commandTargetAvailable = commandsAllowed && handshake.connected && handshake.runId !== undefined
  const workerAcceptsChanges = worker.state === 'starting' || worker.state === 'running' || worker.state === 'waiting'
  const actions = commandTargetAvailable ? DIRECT_ACTIONS.filter((definition) => (
    hasCapability(handshake.capabilities, definition.action)
    && (definition.workerStates?.includes(worker.state) ?? true)
    && (definition.action !== 'resume' || latestWorkerStatus(worker) === 'paused')
  )) : []
  const inlineCommandsAvailable = commandTargetAvailable && workerAcceptsChanges
  const confirmation = DIRECT_ACTIONS.find((definition) => definition.action === confirmingAction)

  useEffect(() => {
    setPendingAction(null)
    setConfirmingAction(null)
    setReceipt(null)
    setError(null)
  }, [worker.workerId])

  async function issue(action: OperatorAction, payload: Readonly<Record<string, unknown>> = {}): Promise<boolean> {
    setConfirmingAction(null)
    setPendingAction(action)
    setReceipt(null)
    setError(null)
    try {
      const nextReceipt = await dispatch({
        action,
        agentId: worker.workerId,
        packetId: worker.packetId,
        ...(selectedModuleId === null ? {} : { moduleId: selectedModuleId }),
        payload
      })
      setReceipt(nextReceipt)
      return nextReceipt.status !== 'rejected'
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
      return false
    } finally {
      setPendingAction(null)
    }
  }

  return (
    <aside className="command-panel" aria-label="Agent 操作">
      <header>
        <div>
          <span className={`worker-dot worker-${worker.state}`} />
          <div><strong>{roleLabel(worker.role)}</strong><small>{shortPacketId(worker.packetId)} · 第 {worker.attempt} 次尝试</small></div>
        </div>
        <button type="button" className="icon-button" onClick={onClose} aria-label="关闭操作面板"><Icon name="close" size={19} /></button>
      </header>

      <section className="selected-module-card">
        <span>操作目标</span>
        <strong>{selectedModule?.label ?? '整个 Worker'}</strong>
        <small>{selectedModule === undefined ? '未选中具体模块' : `模块状态：${selectedModule.state}`}</small>
      </section>

      {!commandsAllowed ? (
        <div className="panel-unavailable"><Icon name="warning" size={18} />演示快照只读，不能发送执行命令。</div>
      ) : !handshake.connected ? (
        <div className="panel-unavailable"><Icon name="warning" size={18} />{handshake.unavailableReason ?? '本地引擎未连接'}</div>
      ) : handshake.runId === undefined ? (
        <div className="panel-unavailable"><Icon name="warning" size={18} />引擎未提供权威运行编号。</div>
      ) : actions.length === 0 && (!inlineCommandsAvailable || (!handshake.capabilities.appendPrompt && !handshake.capabilities.insertModule)) ? (
        <div className="panel-unavailable">引擎没有为此 Worker 开放操作能力。这里只展示真实状态。</div>
      ) : null}

      {actions.length > 0 ? (
        <section className="command-actions">
          <h3>运行控制</h3>
          {actions.map((definition) => (
            <button
              type="button"
              key={definition.action}
              className={`command-action${definition.tone === 'danger' ? ' danger' : ''}`}
              disabled={pendingAction !== null}
              onClick={() => {
                if (definition.requiresConfirmation) setConfirmingAction(definition.action)
                else void issue(definition.action)
              }}
            >
              <Icon name={definition.icon} size={18} />
              <span>{definition.label}</span>
              <Icon name="chevron" size={16} />
            </button>
          ))}
        </section>
      ) : null}

      {confirmation === undefined ? null : (
        <section className="command-confirmation" role="alertdialog" aria-modal="true" aria-label={`确认${confirmation.label}`}>
          <strong>{confirmation.label}？</strong>
          <p>{confirmation.action === 'stop_keep_memory' ? '只有引擎确认 checkpoint 已持久化后，界面才会显示任务已停止。' : '当前执行尝试将停止；已有事件和证据不会被删除。'}</p>
          <div>
            <button type="button" className="secondary-button" onClick={() => setConfirmingAction(null)}>取消</button>
            <button type="button" className="danger-button" onClick={() => void issue(confirmation.action)}>确认停止</button>
          </div>
        </section>
      )}

      {inlineCommandsAvailable && handshake.capabilities.appendPrompt ? (
        <InlineCommandComposer
          label="追加说明"
          placeholder="说明新约束或补充上下文"
          buttonLabel="加入 Prompt"
          icon="prompt"
          submitting={pendingAction !== null}
          onSubmit={(prompt) => issue('append_prompt', { prompt })}
        />
      ) : null}

      {inlineCommandsAvailable && handshake.capabilities.insertModule ? (
        <InlineCommandComposer
          label="增加执行模块"
          placeholder="描述需要新增的步骤"
          buttonLabel="提交变更"
          icon="insert"
          submitting={pendingAction !== null}
          onSubmit={(instruction) => issue('insert_module', {
            instruction,
            ...(selectedModuleId === null ? {} : { beforeModuleId: selectedModuleId })
          })}
        />
      ) : null}

      <div className="command-feedback" aria-live="polite">
        {pendingAction !== null ? <span><Icon name="clock" size={17} />正在等待引擎回执…</span> : null}
        {receipt !== null ? <span className={`receipt-${receipt.status}`}><Icon name={receipt.status === 'rejected' ? 'warning' : receipt.status === 'applied' ? 'check' : 'clock'} size={17} />{receiptCopy(receipt)}</span> : null}
        {error !== null ? <span className="inline-error"><Icon name="warning" size={17} />命令发送失败：{error}</span> : null}
      </div>
      <p className="command-footnote">按钮只发送命令。界面不会在收到引擎回执前把任务标成已暂停、已停止或已回退。</p>
    </aside>
  )
}
