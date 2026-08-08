import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import {
  MAX_DRAFT_ATTACHMENTS,
  MAX_REQUIREMENT_DRAFT_CHARS,
  type CommandReceipt,
  type DraftAttachment,
  type RequirementDraftSnapshot
} from '../shared/protocol'
import type { CommandDispatcher } from '../renderer/src/command-types'
import { Icon } from '../panels/Common'

const EMPTY_DRAFT: RequirementDraftSnapshot = { text: '', attachments: [] }
const SAVE_DELAY_MS = 300

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

function shouldCarryDraft(previousScope: string, nextScope: string): boolean {
  return nextScope.startsWith('project:') &&
    (previousScope === 'unassigned' || previousScope.startsWith('fixture:'))
}

export function RequirementComposer({
  canSubmit,
  canAddFiles,
  unavailableReason,
  sourceId,
  dispatch,
  selectDraftFiles,
  selectDraftFolders,
  loadRequirementDraft,
  saveRequirementDraft,
  moveRequirementDraft,
  discardDraftAttachment
}: {
  readonly canSubmit: boolean
  readonly canAddFiles: boolean
  readonly unavailableReason?: string
  readonly sourceId: string | null
  readonly dispatch: CommandDispatcher
  readonly selectDraftFiles: (scopeId: string) => Promise<RequirementDraftSnapshot>
  readonly selectDraftFolders: (scopeId: string) => Promise<RequirementDraftSnapshot>
  readonly loadRequirementDraft: (scopeId: string) => Promise<RequirementDraftSnapshot>
  readonly saveRequirementDraft: (scopeId: string, draft: RequirementDraftSnapshot) => Promise<void>
  readonly moveRequirementDraft: (sourceScopeId: string, targetScopeId: string) => Promise<RequirementDraftSnapshot>
  readonly discardDraftAttachment: (scopeId: string, attachmentId: DraftAttachment['id']) => Promise<RequirementDraftSnapshot>
}): ReactNode {
  const draftScope = sourceId ?? 'unassigned'
  const activeScope = useRef<string | null>(null)
  const drafts = useRef(new Map<string, RequirementDraftSnapshot>())
  const loadGeneration = useRef(0)
  const saveTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const [draft, setDraft] = useState<RequirementDraftSnapshot>(EMPTY_DRAFT)
  const [loadingDraft, setLoadingDraft] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [selectingKind, setSelectingKind] = useState<DraftAttachment['kind'] | null>(null)
  const [removingAttachment, setRemovingAttachment] = useState<string | null>(null)
  const [receipt, setReceipt] = useState<CommandReceipt | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const generation = ++loadGeneration.current
    const previousScope = activeScope.current
    const previousDraft = previousScope === null ? undefined : drafts.current.get(previousScope)
    if (saveTimer.current !== undefined) clearTimeout(saveTimer.current)
    saveTimer.current = undefined
    setLoadingDraft(true)
    setReceipt(null)
    setError(null)

    void (async () => {
      if (previousScope !== null && previousDraft !== undefined) {
        await saveRequirementDraft(previousScope, previousDraft)
      }
      const next = previousScope !== null && shouldCarryDraft(previousScope, draftScope)
        ? await moveRequirementDraft(previousScope, draftScope)
        : await loadRequirementDraft(draftScope)
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
  }, [draftScope, loadRequirementDraft, moveRequirementDraft, saveRequirementDraft])

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
    if (!canAddFiles || scope === null) return
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
          draftScope: scope,
          attachments: submittedDraft.attachments
        }
      })
      if (activeScope.current === scope) setReceipt(nextReceipt)
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
      <label htmlFor="requirement-input">
        <span className="composer-role-avatar">产</span>
        <span><strong>向产品需求经理交代目标</strong><small>先澄清使用者、范围和验收标准，再组织完整研发团队。</small></span>
      </label>
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
        placeholder={loadingDraft ? '正在读取本地草稿…' : '描述要交付的软件、目标用户、关键流程和你认定的完成标准。'}
        rows={6}
      />
      <div className="composer-reference-toolbar">
        <button
          className="secondary-button compact-button reference-button"
          type="button"
          disabled={!canAddFiles || loadingDraft || selectingKind !== null || draft.attachments.length >= MAX_DRAFT_ATTACHMENTS}
          onClick={() => void addAttachment('file')}
          title={canAddFiles ? '从电脑任意位置选择文件，并复制到当前需求草稿的本地附件区' : '草稿尚未就绪'}
        >
          <Icon name="file" size={17} />{selectingKind === 'file' ? '正在安全复制…' : '添加文件'}
        </button>
        <button
          className="secondary-button compact-button reference-button"
          type="button"
          disabled={!canAddFiles || loadingDraft || selectingKind !== null || draft.attachments.length >= MAX_DRAFT_ATTACHMENTS}
          onClick={() => void addAttachment('folder')}
          title={canAddFiles ? '从电脑任意位置选择文件夹，并按原目录结构复制到本地附件区' : '草稿尚未就绪'}
        >
          <Icon name="folder" size={17} />{selectingKind === 'folder' ? '正在安全复制…' : '添加文件夹'}
        </button>
        <span>支持任意文件类型与文件夹；内容会安全复制并保存在本机，不会上传。</span>
      </div>
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
      <div className="composer-footer">
        <div className="composer-status" aria-live="polite">
          {error !== null ? (
            <span className="inline-error">操作失败：{error}</span>
          ) : !canSubmit ? (
            <span className="muted-message"><Icon name="warning" size={17} />草稿和附件已保存在本机；{unavailableReason ?? '当前引擎未开放需求接收能力'}，暂时不能派单。</span>
          ) : receipt !== null ? (
            <span className={`receipt-message receipt-${receipt.status}`}>{receiptText(receipt)}</span>
          ) : (
            <span>{draft.attachments.length === 0 ? '正式派单会交给产品需求经理（Intake）；只在收到引擎回执后显示已受理。' : `正式派单时会把 ${draft.attachments.length} 个本地附件的可读副本交给 Intake。`}</span>
          )}
        </div>
        <button
          className="primary-button send-button"
          type="submit"
          disabled={!canSubmit || loadingDraft || submitting || draft.text.trim() === '' || (receipt !== null && receipt.status !== 'rejected')}
          title={canSubmit ? undefined : unavailableReason}
        >
          {submitting ? '等待引擎回执…' : '交给产品需求经理'}<Icon name="send" size={18} />
        </button>
      </div>
    </form>
  )
}
