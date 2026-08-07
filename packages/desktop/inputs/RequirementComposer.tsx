import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { MAX_PROJECT_FILE_REFERENCES, type CommandReceipt, type ProjectFileReference } from '../shared/protocol'
import type { CommandDispatcher } from '../renderer/src/command-types'
import { Icon } from '../panels/Common'

function receiptText(receipt: CommandReceipt): string {
  if (receipt.status === 'applied') return '需求已由引擎应用，正在等待新的项目状态。'
  if (receipt.status === 'waiting_safe_point') return '引擎已受理，正在等待安全执行点。'
  if (receipt.status === 'accepted') return '引擎已受理；最终结果以新的权威状态为准。'
  return `引擎拒绝了请求${receipt.reason === undefined ? '' : `：${receipt.reason}`}`
}

interface RequirementDraft {
  readonly value: string
  readonly references: readonly ProjectFileReference[]
}

const EMPTY_DRAFT: RequirementDraft = { value: '', references: [] }

function sameDraft(left: RequirementDraft, right: RequirementDraft): boolean {
  return left.value === right.value &&
    left.references.length === right.references.length &&
    left.references.every((reference, index) => {
      const other = right.references[index]
      return other !== undefined &&
        reference.path === other.path &&
        reference.sizeBytes === other.sizeBytes &&
        reference.sha256 === other.sha256
    })
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`
}

export function RequirementComposer({ canSubmit, canReferenceFiles, unavailableReason, sourceId, dispatch, selectProjectFiles }: {
  readonly canSubmit: boolean
  readonly canReferenceFiles: boolean
  readonly unavailableReason?: string
  readonly sourceId: string | null
  readonly dispatch: CommandDispatcher
  readonly selectProjectFiles: (sourceId: string) => Promise<readonly ProjectFileReference[]>
}): ReactNode {
  const draftKey = sourceId ?? 'unassigned'
  const drafts = useRef(new Map<string, RequirementDraft>())
  const activeDraftKey = useRef(draftKey)
  const [draft, setDraft] = useState<RequirementDraft>(EMPTY_DRAFT)
  const [submitting, setSubmitting] = useState(false)
  const [selectingFiles, setSelectingFiles] = useState(false)
  const [receipt, setReceipt] = useState<CommandReceipt | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (activeDraftKey.current === draftKey) return
    const previousKey = activeDraftKey.current
    activeDraftKey.current = draftKey
    const previousDraft = drafts.current.get(previousKey)
    const shouldCarryUnboundDraft = sourceId?.startsWith('project:') === true &&
      (previousKey === 'unassigned' || previousKey.startsWith('fixture:'))
    const nextDraft = drafts.current.get(draftKey)
      ?? (shouldCarryUnboundDraft ? previousDraft : undefined)
      ?? EMPTY_DRAFT
    drafts.current.set(draftKey, nextDraft)
    setDraft(nextDraft)
    setReceipt(null)
    setError(null)
  }, [draftKey, sourceId])

  function updateDraft(updater: (current: RequirementDraft) => RequirementDraft): void {
    setDraft((current) => {
      const next = updater(current)
      drafts.current.set(draftKey, next)
      return next
    })
    setReceipt(null)
    setError(null)
  }

  async function addReferences(): Promise<void> {
    if (!canReferenceFiles || sourceId === null) return
    const selectionDraftKey = draftKey
    setSelectingFiles(true)
    setError(null)
    try {
      const selected = await selectProjectFiles(sourceId)
      if (selected.length === 0) return
      const current = drafts.current.get(selectionDraftKey) ?? draft
      const byPath = new Map(current.references.map((reference) => [reference.path, reference]))
      for (const reference of selected) byPath.set(reference.path, reference)
      if (byPath.size > MAX_PROJECT_FILE_REFERENCES) {
        throw new Error(`一项需求最多引用 ${MAX_PROJECT_FILE_REFERENCES} 个文件。`)
      }
      const next = { ...current, references: [...byPath.values()] }
      drafts.current.set(selectionDraftKey, next)
      if (activeDraftKey.current === selectionDraftKey) {
        setDraft(next)
        setReceipt(null)
      }
    } catch (reason) {
      if (activeDraftKey.current === selectionDraftKey) {
        setError(reason instanceof Error ? reason.message : String(reason))
      }
    } finally {
      setSelectingFiles(false)
    }
  }

  async function submit(event: FormEvent): Promise<void> {
    event.preventDefault()
    const requirement = draft.value.trim()
    if (!canSubmit || sourceId === null || requirement === '') return
    const submittedDraftKey = draftKey
    const submittedDraft = draft
    setSubmitting(true)
    setReceipt(null)
    setError(null)
    try {
      const nextReceipt = await dispatch({
        action: 'submit_requirement',
        agentId: 'intake',
        payload: {
          requirement,
          ...(draft.references.length === 0 ? {} : { references: draft.references })
        }
      })
      if (activeDraftKey.current === submittedDraftKey) setReceipt(nextReceipt)
      if (nextReceipt.status === 'applied') {
        const current = drafts.current.get(submittedDraftKey) ?? EMPTY_DRAFT
        if (sameDraft(current, submittedDraft)) {
          drafts.current.set(submittedDraftKey, EMPTY_DRAFT)
          if (activeDraftKey.current === submittedDraftKey) setDraft(EMPTY_DRAFT)
        }
      }
    } catch (reason) {
      if (activeDraftKey.current === submittedDraftKey) {
        setError(reason instanceof Error ? reason.message : String(reason))
      }
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
        value={draft.value}
        onChange={(event) => updateDraft((current) => ({ ...current, value: event.target.value }))}
        placeholder="描述要交付的软件、目标用户、关键流程和你认定的完成标准。"
        rows={6}
      />
      <div className="composer-reference-toolbar">
        <button
          className="secondary-button compact-button reference-button"
          type="button"
          disabled={!canReferenceFiles || sourceId === null || selectingFiles}
          onClick={() => void addReferences()}
          title={canReferenceFiles ? '只引用当前项目内的文件，不上传副本' : '请先打开本地项目'}
        >
          <Icon name="folder" size={17} />{selectingFiles ? '正在校验…' : '引用项目文件'}
        </button>
        <span>仅记录项目内相对路径、大小与 SHA-256；不会复制或上传文件。</span>
      </div>
      {draft.references.length === 0 ? null : (
        <ul className="file-reference-list" aria-label="已引用的项目文件">
          {draft.references.map((reference) => (
            <li key={reference.path}>
              <span className="file-reference-icon"><Icon name="folder" size={16} /></span>
              <span><strong>{reference.path}</strong><small>{formatFileSize(reference.sizeBytes)} · SHA-256 {reference.sha256.slice(0, 12)}…</small></span>
              <button
                className="icon-button"
                type="button"
                aria-label={`移除 ${reference.path}`}
                title="移除引用"
                onClick={() => updateDraft((current) => ({ ...current, references: current.references.filter((item) => item.path !== reference.path) }))}
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
            <span className="muted-message"><Icon name="warning" size={17} />草稿可以继续编辑；{unavailableReason ?? '当前引擎未开放需求接收能力'}，暂时不能派单。</span>
          ) : receipt !== null ? (
            <span className={`receipt-message receipt-${receipt.status}`}>{receiptText(receipt)}</span>
          ) : (
            <span>{draft.references.length === 0 ? '正式派单会交给产品需求经理（Intake）；只在收到引擎回执后显示已受理。' : `正式派单时会把 ${draft.references.length} 个已验证文件引用交给 Intake。`}</span>
          )}
        </div>
        <button
          className="primary-button send-button"
          type="submit"
          disabled={!canSubmit || submitting || draft.value.trim() === '' || (receipt !== null && receipt.status !== 'rejected')}
          title={canSubmit ? undefined : unavailableReason}
        >
          {submitting ? '等待引擎回执…' : '交给产品需求经理'}<Icon name="send" size={18} />
        </button>
      </div>
    </form>
  )
}
