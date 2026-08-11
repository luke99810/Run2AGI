import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import {
  MAX_DRAFT_ATTACHMENTS,
  MAX_REQUIREMENT_DRAFT_CHARS,
  type CommandReceipt,
  type DraftAttachment,
  type RequirementDraftSnapshot
} from '../shared/protocol'
import type { CommandDispatcher } from '../renderer/src/command-types'
import {
  KNOWLEDGE_OPTIONS,
  PLUGIN_OPTIONS,
  SKILL_OPTIONS,
  toggleSelection,
  type ResourceOption,
  type TaskContextSelection,
  type TaskHistoryEntry
} from '../renderer/src/task-library'
import { Icon } from '../panels/Common'

const EMPTY_DRAFT: RequirementDraftSnapshot = { text: '', attachments: [] }
const SAVE_DELAY_MS = 300
const ACCESS_OPTIONS = [
  { id: 'read_only', label: '只读', detail: '仅分析项目和附件' },
  { id: 'workspace_write', label: '工作区写入', detail: '允许在授权路径内修改' },
  { id: 'full_access', label: '完全访问', detail: '请求更高权限，仍受 Guardian 限制' }
] as const

function accessLabel(mode: TaskContextSelection['accessMode']): string {
  return ACCESS_OPTIONS.find((option) => option.id === mode)?.label ?? '访问权限'
}

function ResourceChecks({ label, options, selected, onToggle }: {
  readonly label: string
  readonly options: readonly ResourceOption[]
  readonly selected: readonly string[]
  readonly onToggle: (id: string) => void
}): ReactNode {
  return (
    <fieldset className="resource-checks">
      <legend>{label}</legend>
      {options.map((option) => (
        <label key={option.id}>
          <input type="checkbox" checked={selected.includes(option.id)} onChange={() => onToggle(option.id)} />
          <span><strong>{option.label}</strong><small>{option.detail}</small></span>
          {option.availability === 'pending_runtime' ? <em>待接入</em> : null}
        </label>
      ))}
    </fieldset>
  )
}

function receiptText(receipt: CommandReceipt): string {
  if (receipt.status === 'applied') return '需求已由引擎应用，正在等待新的项目状态。'
  if (receipt.status === 'waiting_safe_point') return '引擎已受理，正在等待安全执行点。'
  if (receipt.status === 'accepted') return '引擎已受理；最终结果以新的权威状态为准。'
  return `引擎拒绝了请求${receipt.reason === undefined ? '' : `：${receipt.reason}`}`
}

function sameDraft(left: RequirementDraftSnapshot, right: RequirementDraftSnapshot): boolean {
  return left.text === right.text &&
    left.attachments.length === right.attachments.length &&
    left.attachments.every((attachment, index) => {
      const other = right.attachments[index]
      return other !== undefined &&
        attachment.id === other.id &&
        attachment.name === other.name &&
        attachment.kind === other.kind &&
        attachment.fileCount === other.fileCount &&
        attachment.sizeBytes === other.sizeBytes &&
        attachment.sha256 === other.sha256
    })
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`
}

export function RequirementComposer({
  canSubmit,
  unavailableReason,
  taskId,
  draftScope,
  legacyScope,
  taskContext,
  taskHistory,
  onDraftChange,
  onAttachmentNamesChange,
  onContextChange,
  onSubmitted,
  dispatch,
  selectDraftFiles,
  selectDraftFolders,
  loadRequirementDraft,
  saveRequirementDraft,
  moveRequirementDraft,
  discardDraftAttachment
}: {
  readonly canSubmit: boolean
  readonly unavailableReason?: string
  readonly taskId: string
  readonly draftScope: string
  readonly legacyScope: string
  readonly taskContext: TaskContextSelection
  readonly taskHistory: readonly TaskHistoryEntry[]
  readonly onDraftChange: (text: string) => void
  readonly onAttachmentNamesChange: (names: readonly string[]) => void
  readonly onContextChange: (context: TaskContextSelection) => void
  readonly onSubmitted: () => void
  readonly dispatch: CommandDispatcher
  readonly selectDraftFiles: (scopeId: string) => Promise<RequirementDraftSnapshot>
  readonly selectDraftFolders: (scopeId: string) => Promise<RequirementDraftSnapshot>
  readonly loadRequirementDraft: (scopeId: string) => Promise<RequirementDraftSnapshot>
  readonly saveRequirementDraft: (scopeId: string, draft: RequirementDraftSnapshot) => Promise<void>
  readonly moveRequirementDraft: (sourceScopeId: string, targetScopeId: string) => Promise<RequirementDraftSnapshot>
  readonly discardDraftAttachment: (scopeId: string, attachmentId: DraftAttachment['id']) => Promise<RequirementDraftSnapshot>
}): ReactNode {
  const activeScope = useRef<string | null>(null)
  const drafts = useRef(new Map<string, RequirementDraftSnapshot>())
  const loadGeneration = useRef(0)
  const saveTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const attachmentMenuRef = useRef<HTMLDivElement>(null)
  const accessMenuRef = useRef<HTMLDivElement>(null)
  const contextMenuRef = useRef<HTMLDivElement>(null)
  const [draft, setDraft] = useState<RequirementDraftSnapshot>(EMPTY_DRAFT)
  const [loadingDraft, setLoadingDraft] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [selectingKind, setSelectingKind] = useState<DraftAttachment['kind'] | null>(null)
  const [removingAttachment, setRemovingAttachment] = useState<string | null>(null)
  const [receipt, setReceipt] = useState<CommandReceipt | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [attachmentMenuOpen, setAttachmentMenuOpen] = useState(false)
  const [accessMenuOpen, setAccessMenuOpen] = useState(false)
  const [contextMenuOpen, setContextMenuOpen] = useState(false)

  useEffect(() => {
    if (!attachmentMenuOpen && !accessMenuOpen && !contextMenuOpen) return
    function closeMenu(event: PointerEvent): void {
      const target = event.target as Node
      if (!attachmentMenuRef.current?.contains(target)) setAttachmentMenuOpen(false)
      if (!accessMenuRef.current?.contains(target)) setAccessMenuOpen(false)
      if (!contextMenuRef.current?.contains(target)) setContextMenuOpen(false)
    }
    function closeOnEscape(event: KeyboardEvent): void {
      if (event.key === 'Escape') {
        setAttachmentMenuOpen(false)
        setAccessMenuOpen(false)
        setContextMenuOpen(false)
      }
    }
    document.addEventListener('pointerdown', closeMenu)
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.removeEventListener('pointerdown', closeMenu)
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [accessMenuOpen, attachmentMenuOpen, contextMenuOpen])

  useEffect(() => {
    const generation = ++loadGeneration.current
    const previousScope = activeScope.current
    const previousDraft = previousScope === null ? undefined : drafts.current.get(previousScope)
    if (saveTimer.current !== undefined) clearTimeout(saveTimer.current)
    saveTimer.current = undefined
    setLoadingDraft(true)
    setAttachmentMenuOpen(false)
    setAccessMenuOpen(false)
    setContextMenuOpen(false)
    setReceipt(null)
    setError(null)

    void (async () => {
      if (previousScope !== null && previousDraft !== undefined) {
        await saveRequirementDraft(previousScope, previousDraft)
      }
      let next = await loadRequirementDraft(draftScope)
      if (
        previousScope === null &&
        legacyScope !== draftScope &&
        next.text === '' &&
        next.attachments.length === 0
      ) {
        next = await moveRequirementDraft(legacyScope, draftScope)
      }
      if (loadGeneration.current !== generation) return
      activeScope.current = draftScope
      drafts.current.set(draftScope, next)
      setDraft(next)
      setLoadingDraft(false)
    })().catch((reason: unknown) => {
      if (loadGeneration.current !== generation) return
      activeScope.current = draftScope
      setLoadingDraft(false)
      setError(reason instanceof Error ? reason.message : String(reason))
    })

    return () => {
      if (saveTimer.current !== undefined) clearTimeout(saveTimer.current)
      saveTimer.current = undefined
    }
  }, [draftScope, legacyScope, loadRequirementDraft, moveRequirementDraft, saveRequirementDraft])

  function persistLater(scope: string, next: RequirementDraftSnapshot): void {
    if (saveTimer.current !== undefined) clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(() => {
      saveTimer.current = undefined
      void saveRequirementDraft(scope, next).catch((reason: unknown) => {
        if (activeScope.current === scope) setError(reason instanceof Error ? reason.message : String(reason))
      })
    }, SAVE_DELAY_MS)
  }

  function updateText(text: string): void {
    const scope = activeScope.current
    if (scope === null) return
    const next = { ...draft, text }
    drafts.current.set(scope, next)
    setDraft(next)
    onDraftChange(text)
    setReceipt(null)
    setError(null)
    persistLater(scope, next)
  }

  async function persistNow(scope: string, value: RequirementDraftSnapshot): Promise<void> {
    if (saveTimer.current !== undefined) clearTimeout(saveTimer.current)
    saveTimer.current = undefined
    await saveRequirementDraft(scope, value)
  }

  async function addAttachment(kind: DraftAttachment['kind']): Promise<void> {
    const scope = activeScope.current
    if (scope === null) return
    setAttachmentMenuOpen(false)
    setSelectingKind(kind)
    setError(null)
    try {
      await persistNow(scope, draft)
      const next = kind === 'file'
        ? await selectDraftFiles(scope)
        : await selectDraftFolders(scope)
      drafts.current.set(scope, next)
      if (activeScope.current === scope) {
        setDraft(next)
        onAttachmentNamesChange(next.attachments.map((attachment) => attachment.name))
        setReceipt(null)
      }
    } catch (reason) {
      if (activeScope.current === scope) setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setSelectingKind(null)
    }
  }

  async function removeAttachment(attachment: DraftAttachment): Promise<void> {
    const scope = activeScope.current
    if (scope === null) return
    setRemovingAttachment(attachment.id)
    setError(null)
    try {
      await persistNow(scope, draft)
      const next = await discardDraftAttachment(scope, attachment.id)
      drafts.current.set(scope, next)
      if (activeScope.current === scope) {
        setDraft(next)
        onAttachmentNamesChange(next.attachments.map((item) => item.name))
        setReceipt(null)
      }
    } catch (reason) {
      if (activeScope.current === scope) setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setRemovingAttachment(null)
    }
  }

  async function submit(event: FormEvent): Promise<void> {
    event.preventDefault()
    const scope = activeScope.current
    const requirement = draft.text.trim()
    if (!canSubmit || scope === null || requirement === '') return
    const submittedDraft = draft
    setSubmitting(true)
    setReceipt(null)
    setError(null)
    try {
      await persistNow(scope, submittedDraft)
      const nextReceipt = await dispatch({
        action: 'submit_requirement',
        agentId: 'intake',
        payload: {
          requirement,
          taskId,
          draftScope: scope,
          attachments: submittedDraft.attachments,
          requestedAccessMode: taskContext.accessMode,
          pluginIds: taskContext.pluginIds,
          knowledgeIds: taskContext.knowledgeIds,
          skillIds: taskContext.skillIds,
          relatedTaskIds: taskContext.relatedTaskIds,
          taskHistory
        }
      })
      if (activeScope.current === scope) setReceipt(nextReceipt)
      if (nextReceipt.status !== 'rejected') onSubmitted()
      if (nextReceipt.status === 'applied') {
        const current = drafts.current.get(scope) ?? EMPTY_DRAFT
        if (sameDraft(current, submittedDraft)) {
          await saveRequirementDraft(scope, EMPTY_DRAFT)
          drafts.current.set(scope, EMPTY_DRAFT)
          if (activeScope.current === scope) setDraft(EMPTY_DRAFT)
        }
      }
    } catch (reason) {
      if (activeScope.current === scope) setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form className="requirement-composer" onSubmit={(event) => void submit(event)}>
      <label className="sr-only" htmlFor="requirement-input">需求描述</label>
      <textarea
        id="requirement-input"
        value={draft.text}
        maxLength={MAX_REQUIREMENT_DRAFT_CHARS}
        disabled={loadingDraft}
        onChange={(event) => updateText(event.target.value)}
        onBlur={() => {
          const scope = activeScope.current
          if (scope !== null) void persistNow(scope, draft).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : String(reason)))
        }}
        placeholder={loadingDraft ? '正在读取本地草稿…' : '描述你要交付的软件…'}
        rows={6}
      />
      {draft.attachments.length === 0 ? null : (
        <ul className="file-reference-list" aria-label="已添加的文件和文件夹">
          {draft.attachments.map((attachment) => (
            <li key={attachment.id}>
              <span className="file-reference-icon"><Icon name={attachment.kind === 'folder' ? 'folder' : 'file'} size={16} /></span>
              <span><strong>{attachment.name}</strong><small>{attachment.kind === 'folder' ? `${attachment.fileCount} 个文件 · ` : ''}{formatFileSize(attachment.sizeBytes)} · SHA-256 {attachment.sha256.slice(0, 12)}…</small></span>
              <button
                className="icon-button"
                type="button"
                disabled={removingAttachment === attachment.id}
                aria-label={`移除 ${attachment.name}`}
                title="从草稿中移除文件"
                onClick={() => void removeAttachment(attachment)}
              ><Icon name="close" size={17} /></button>
            </li>
          ))}
        </ul>
      )}
      <div className="composer-actions">
        <div className="attachment-menu-wrap" ref={attachmentMenuRef}>
          <button
            className="icon-button attachment-trigger"
            type="button"
            aria-label="添加附件"
            aria-haspopup="menu"
            aria-expanded={attachmentMenuOpen}
            disabled={loadingDraft || selectingKind !== null || draft.attachments.length >= MAX_DRAFT_ATTACHMENTS}
            onClick={() => {
              setAccessMenuOpen(false)
              setContextMenuOpen(false)
              setAttachmentMenuOpen((open) => !open)
            }}
            title="添加附件"
          ><Icon name="plus" size={21} /></button>
          {attachmentMenuOpen ? (
            <div className="attachment-menu" role="menu" aria-label="添加附件">
              <button type="button" role="menuitem" onClick={() => void addAttachment('file')}>
                <Icon name="file" size={18} /><span><strong>添加文件</strong><small>从电脑任意位置选择</small></span>
              </button>
              <button type="button" role="menuitem" onClick={() => void addAttachment('folder')}>
                <Icon name="folder" size={18} /><span><strong>添加文件夹</strong><small>保留目录结构</small></span>
              </button>
            </div>
          ) : null}
        </div>
        <div className="composer-menu-wrap" ref={accessMenuRef}>
          <button
            className="composer-menu-trigger"
            type="button"
            aria-haspopup="menu"
            aria-expanded={accessMenuOpen}
            onClick={() => {
              setAttachmentMenuOpen(false)
              setContextMenuOpen(false)
              setAccessMenuOpen((open) => !open)
            }}
          >
            <Icon name="shield" size={17} /><span>{accessLabel(taskContext.accessMode)}</span>
          </button>
          {accessMenuOpen ? (
            <div className="composer-popover access-menu" role="menu" aria-label="访问权限">
              <header><strong>访问权限</strong><small>最终上限由 RoleSpec 与 Guardian 决定</small></header>
              {ACCESS_OPTIONS.map((option) => (
                <button
                  type="button"
                  role="menuitemradio"
                  aria-checked={taskContext.accessMode === option.id}
                  key={option.id}
                  onClick={() => {
                    onContextChange({ ...taskContext, accessMode: option.id })
                    setAccessMenuOpen(false)
                  }}
                >
                  <span><strong>{option.label}</strong><small>{option.detail}</small></span>
                  {taskContext.accessMode === option.id ? <Icon name="check" size={17} /> : null}
                </button>
              ))}
            </div>
          ) : null}
        </div>
        <div className="composer-menu-wrap" ref={contextMenuRef}>
          <button
            className="composer-menu-trigger"
            type="button"
            aria-haspopup="menu"
            aria-expanded={contextMenuOpen}
            onClick={() => {
              setAttachmentMenuOpen(false)
              setAccessMenuOpen(false)
              setContextMenuOpen((open) => !open)
            }}
          >
            <Icon name="book" size={17} /><span>上下文</span>
            <em>{taskContext.pluginIds.length + taskContext.knowledgeIds.length + taskContext.skillIds.length}</em>
          </button>
          {contextMenuOpen ? (
            <div className="composer-popover context-menu" role="dialog" aria-label="任务上下文">
              <header><strong>任务上下文</strong><small>选择结果会随需求交给中心 Agent</small></header>
              <ResourceChecks
                label="知识库"
                options={KNOWLEDGE_OPTIONS}
                selected={taskContext.knowledgeIds}
                onToggle={(id) => onContextChange({ ...taskContext, knowledgeIds: toggleSelection(taskContext.knowledgeIds, id) })}
              />
              <ResourceChecks
                label="Skills"
                options={SKILL_OPTIONS}
                selected={taskContext.skillIds}
                onToggle={(id) => onContextChange({ ...taskContext, skillIds: toggleSelection(taskContext.skillIds, id) })}
              />
              <ResourceChecks
                label="插件"
                options={PLUGIN_OPTIONS}
                selected={taskContext.pluginIds}
                onToggle={(id) => onContextChange({ ...taskContext, pluginIds: toggleSelection(taskContext.pluginIds, id) })}
              />
            </div>
          ) : null}
        </div>
        <span className="composer-attachment-count">{selectingKind !== null ? '正在校验…' : draft.attachments.length > 0 ? `${draft.attachments.length} 个附件` : ''}</span>
        <button
          className="send-button"
          type="submit"
          disabled={!canSubmit || loadingDraft || submitting || draft.text.trim() === '' || (receipt !== null && receipt.status !== 'rejected')}
          aria-label="提交需求"
          title={canSubmit ? '提交需求' : unavailableReason}
        >
          <Icon name="send" size={19} />
        </button>
      </div>
      <div className="composer-status" aria-live="polite">
        {error !== null ? (
          <span className="inline-error">操作失败：{error}</span>
        ) : !canSubmit ? (
          <span className="muted-message"><Icon name="warning" size={16} />草稿已隔离保存，附件直接引用原位置，已准备 {taskHistory.length} 条本地任务摘要；{unavailableReason ?? '当前引擎未开放需求接收能力'}。</span>
        ) : receipt !== null ? (
          <span className={`receipt-message receipt-${receipt.status}`}>{receiptText(receipt)}</span>
        ) : (
          <span>草稿已隔离保存 · 附件直接引用原位置 · 提交时附带 {taskHistory.length} 条本地任务摘要</span>
        )}
      </div>
    </form>
  )
}
