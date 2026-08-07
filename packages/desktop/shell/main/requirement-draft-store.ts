import { createHash, randomUUID } from 'node:crypto'
import { createReadStream, createWriteStream } from 'node:fs'
import { lstat, mkdir, readFile, realpath, rename, rm, stat, writeFile } from 'node:fs/promises'
import { basename, isAbsolute, join, resolve } from 'node:path'
import { Transform } from 'node:stream'
import { pipeline } from 'node:stream/promises'
import {
  MAX_DRAFT_ATTACHMENTS,
  MAX_REQUIREMENT_DRAFT_CHARS,
  type DraftAttachment,
  type OperatorCommand,
  type RequirementDraftSnapshot
} from '../../shared/protocol'

export const MAX_DRAFT_ATTACHMENT_BYTES = 512 * 1024 * 1024
export const MAX_DRAFT_ATTACHMENT_TOTAL_BYTES = 1024 * 1024 * 1024

const MANIFEST_SCHEMA_VERSION = 1
const MAX_MANIFEST_BYTES = 2 * 1024 * 1024
const EMPTY_DRAFT: RequirementDraftSnapshot = { text: '', attachments: [] }

interface StoredDraft extends RequirementDraftSnapshot {
  readonly updatedAt: string
}

interface AttachmentEntry {
  readonly metadata: DraftAttachment
  readonly path: string
}

interface StoredManifest {
  readonly schemaVersion: number
  readonly drafts: Readonly<Record<string, StoredDraft>>
}

export class RequirementDraftStoreError extends Error {
  readonly code: string

  constructor(code: string, message: string) {
    super(message)
    this.name = 'RequirementDraftStoreError'
    this.code = code
  }
}

export class RequirementDraftStore {
  readonly #root: string
  readonly #attachmentDirectory: string
  readonly #manifestPath: string
  readonly #drafts = new Map<string, StoredDraft>()
  readonly #attachments = new Map<string, AttachmentEntry>()
  #operation: Promise<void> = Promise.resolve()

  constructor(root: string) {
    this.#root = resolve(root)
    this.#attachmentDirectory = join(this.#root, 'attachments')
    this.#manifestPath = join(this.#root, 'manifest.json')
  }

  async initialize(): Promise<void> {
    await mkdir(this.#attachmentDirectory, { recursive: true })
    const manifest = await this.#readManifest()
    let changed = false
    for (const [scopeId, draft] of Object.entries(manifest.drafts)) {
      const attachments: DraftAttachment[] = []
      for (const metadata of draft.attachments) {
        const path = this.#attachmentPath(metadata.id)
        const file = await safeLstat(path)
        if (file === undefined || file.isSymbolicLink() || !file.isFile() || file.size !== metadata.sizeBytes) {
          changed = true
          continue
        }
        attachments.push(metadata)
        this.#attachments.set(metadata.id, { metadata, path })
      }
      this.#drafts.set(scopeId, { ...draft, attachments })
    }
    if (changed) await this.#persist()
  }

  load(scopeId: string): Promise<RequirementDraftSnapshot> {
    return this.#exclusive(async () => cloneDraft(this.#drafts.get(assertScopeId(scopeId)) ?? EMPTY_DRAFT))
  }

  save(scopeId: string, draft: RequirementDraftSnapshot): Promise<void> {
    return this.#exclusive(async () => {
      const scope = assertScopeId(scopeId)
      const normalized = this.#validateDraft(draft)
      this.#drafts.set(scope, { ...normalized, updatedAt: new Date().toISOString() })
      await this.#persist()
      await this.#removeUnreferencedAttachments()
    })
  }

  addFiles(scopeId: string, selectedPaths: readonly string[]): Promise<RequirementDraftSnapshot> {
    return this.#exclusive(async () => {
      const scope = assertScopeId(scopeId)
      const current = this.#drafts.get(scope) ?? { ...EMPTY_DRAFT, updatedAt: new Date().toISOString() }
      if (selectedPaths.length + current.attachments.length > MAX_DRAFT_ATTACHMENTS) {
        throw new RequirementDraftStoreError('TOO_MANY_FILES', `一项需求最多添加 ${MAX_DRAFT_ATTACHMENTS} 个文件。`)
      }

      const selected = await uniqueCanonicalFiles(selectedPaths)
      const added: AttachmentEntry[] = []
      let totalBytes = current.attachments.reduce((total, attachment) => total + attachment.sizeBytes, 0)
      try {
        for (const file of selected) {
          const before = await stat(file)
          if (!before.isFile()) throw new RequirementDraftStoreError('FILE_NOT_REGULAR', '只能添加普通文件。')
          if (before.size > MAX_DRAFT_ATTACHMENT_BYTES) {
            throw new RequirementDraftStoreError('FILE_TOO_LARGE', `单个文件不能超过 ${formatMiB(MAX_DRAFT_ATTACHMENT_BYTES)}。`)
          }
          totalBytes += before.size
          if (totalBytes > MAX_DRAFT_ATTACHMENT_TOTAL_BYTES) {
            throw new RequirementDraftStoreError('FILES_TOO_LARGE', `当前草稿的附件合计不能超过 ${formatMiB(MAX_DRAFT_ATTACHMENT_TOTAL_BYTES)}。`)
          }
          const entry = await this.#copyFile(file, before)
          const duplicate = [...current.attachments, ...added.map((item) => item.metadata)].find((attachment) =>
            attachment.name === entry.metadata.name && attachment.sha256 === entry.metadata.sha256
          )
          if (duplicate !== undefined) {
            await rm(entry.path, { force: true })
            totalBytes -= entry.metadata.sizeBytes
            continue
          }
          added.push(entry)
        }
      } catch (error) {
        await Promise.all(added.map((entry) => rm(entry.path, { force: true })))
        throw error
      }

      for (const entry of added) this.#attachments.set(entry.metadata.id, entry)
      const next: StoredDraft = {
        text: current.text,
        attachments: [...current.attachments, ...added.map((entry) => entry.metadata)],
        updatedAt: new Date().toISOString()
      }
      this.#drafts.set(scope, next)
      await this.#persist()
      return cloneDraft(next)
    })
  }

  move(sourceScopeId: string, targetScopeId: string): Promise<RequirementDraftSnapshot> {
    return this.#exclusive(async () => {
      const sourceScope = assertScopeId(sourceScopeId)
      const targetScope = assertScopeId(targetScopeId)
      if (sourceScope === targetScope) return cloneDraft(this.#drafts.get(targetScope) ?? EMPTY_DRAFT)
      const source = this.#drafts.get(sourceScope)
      const target = this.#drafts.get(targetScope)
      if (target !== undefined && (target.text !== '' || target.attachments.length > 0)) return cloneDraft(target)
      if (source === undefined) return cloneDraft(target ?? EMPTY_DRAFT)
      this.#drafts.set(targetScope, { ...source, updatedAt: new Date().toISOString() })
      this.#drafts.delete(sourceScope)
      await this.#persist()
      return cloneDraft(this.#drafts.get(targetScope) ?? EMPTY_DRAFT)
    })
  }

  discard(scopeId: string, attachmentId: string): Promise<RequirementDraftSnapshot> {
    return this.#exclusive(async () => {
      const scope = assertScopeId(scopeId)
      assertAttachmentId(attachmentId)
      const current = this.#drafts.get(scope) ?? { ...EMPTY_DRAFT, updatedAt: new Date().toISOString() }
      const next: StoredDraft = {
        ...current,
        attachments: current.attachments.filter((attachment) => attachment.id !== attachmentId),
        updatedAt: new Date().toISOString()
      }
      this.#drafts.set(scope, next)
      await this.#persist()
      await this.#removeUnreferencedAttachments()
      return cloneDraft(next)
    })
  }

  prepareRequirementCommand(command: OperatorCommand, projectRoot: string): Promise<OperatorCommand> {
    return this.#exclusive(async () => {
      if (command.action !== 'submit_requirement') return command
      const scope = command.payload['draftScope']
      if (typeof scope !== 'string') {
        throw new RequirementDraftStoreError('DRAFT_SCOPE_MISSING', '需求命令缺少草稿作用域。')
      }
      const normalizedScope = assertScopeId(scope)
      const draft = this.#drafts.get(normalizedScope) ?? { ...EMPTY_DRAFT, updatedAt: new Date().toISOString() }
      const requested = command.payload['attachments']
      if (requested !== undefined && !Array.isArray(requested)) {
        throw new RequirementDraftStoreError('ATTACHMENTS_INVALID', '需求附件必须是数组。')
      }
      const attachments = requested === undefined ? [] : requested.map(assertDraftAttachment)
      if (attachments.length > MAX_DRAFT_ATTACHMENTS) {
        throw new RequirementDraftStoreError('TOO_MANY_FILES', `一项需求最多添加 ${MAX_DRAFT_ATTACHMENTS} 个文件。`)
      }

      const engineAttachments: Array<DraftAttachment & { readonly localPath: string }> = []
      for (const metadata of attachments) {
        const owned = draft.attachments.find((attachment) => attachment.id === metadata.id)
        const entry = this.#attachments.get(metadata.id)
        if (owned === undefined || entry === undefined || !sameAttachment(owned, metadata) || !sameAttachment(entry.metadata, metadata)) {
          throw new RequirementDraftStoreError('ATTACHMENT_NOT_OWNED', `草稿附件不存在或已变化：${metadata.name}`)
        }
        const file = await lstat(entry.path)
        if (file.isSymbolicLink() || !file.isFile() || file.size !== metadata.sizeBytes) {
          throw new RequirementDraftStoreError('ATTACHMENT_CHANGED', `本地附件已变化：${metadata.name}`)
        }
        const actualHash = await sha256File(entry.path)
        if (actualHash !== metadata.sha256) {
          throw new RequirementDraftStoreError('ATTACHMENT_CHANGED', `本地附件校验失败：${metadata.name}`)
        }
        engineAttachments.push({ ...metadata, localPath: entry.path })
      }

      return {
        ...command,
        payload: {
          ...command.payload,
          attachments: engineAttachments,
          projectRoot: resolve(projectRoot)
        }
      }
    })
  }

  private #validateDraft(value: RequirementDraftSnapshot): RequirementDraftSnapshot {
    if (!isRecord(value) || typeof value['text'] !== 'string' || value['text'].length > MAX_REQUIREMENT_DRAFT_CHARS) {
      throw new RequirementDraftStoreError('DRAFT_INVALID', `需求草稿不能超过 ${MAX_REQUIREMENT_DRAFT_CHARS} 个字符。`)
    }
    const rawAttachments = value['attachments']
    if (!Array.isArray(rawAttachments) || rawAttachments.length > MAX_DRAFT_ATTACHMENTS) {
      throw new RequirementDraftStoreError('DRAFT_INVALID', `一项需求最多添加 ${MAX_DRAFT_ATTACHMENTS} 个文件。`)
    }
    const attachments = rawAttachments.map(assertDraftAttachment)
    for (const metadata of attachments) {
      const entry = this.#attachments.get(metadata.id)
      if (entry === undefined || !sameAttachment(entry.metadata, metadata)) {
        throw new RequirementDraftStoreError('ATTACHMENT_NOT_FOUND', `附件不存在或已变化：${metadata.name}`)
      }
    }
    return { text: value['text'], attachments }
  }

  private async #copyFile(source: string, before: Awaited<ReturnType<typeof stat>>): Promise<AttachmentEntry> {
    const uuid = randomUUID()
    const id = `attachment:${uuid}`
    const temporaryPath = join(this.#attachmentDirectory, `${uuid}.tmp`)
    const finalPath = this.#attachmentPath(id)
    const hash = createHash('sha256')
    const hasher = new Transform({
      transform(chunk, _encoding, callback) {
        hash.update(chunk)
        callback(null, chunk)
      }
    })
    try {
      await pipeline(
        createReadStream(source),
        hasher,
        createWriteStream(temporaryPath, { flags: 'wx', mode: 0o600 })
      )
      const after = await stat(source)
      if (
        before.size !== after.size || before.mtimeMs !== after.mtimeMs ||
        before.dev !== after.dev || before.ino !== after.ino
      ) {
        throw new RequirementDraftStoreError('FILE_CHANGED', `复制期间文件发生变化：${basename(source)}`)
      }
      await rename(temporaryPath, finalPath)
      return {
        metadata: {
          id,
          name: basename(source),
          sizeBytes: before.size,
          sha256: hash.digest('hex')
        },
        path: finalPath
      }
    } catch (error) {
      await rm(temporaryPath, { force: true })
      await rm(finalPath, { force: true })
      throw error
    }
  }

  private #attachmentPath(id: string): string {
    const uuid = assertAttachmentId(id)
    return join(this.#attachmentDirectory, `${uuid}.data`)
  }

  private async #readManifest(): Promise<StoredManifest> {
    const manifest = await safeLstat(this.#manifestPath)
    if (manifest === undefined) return { schemaVersion: MANIFEST_SCHEMA_VERSION, drafts: {} }
    if (manifest.isSymbolicLink() || !manifest.isFile() || manifest.size > MAX_MANIFEST_BYTES) {
      throw new RequirementDraftStoreError('MANIFEST_INVALID', '本地需求草稿索引不可读取。')
    }
    let value: unknown
    try {
      value = JSON.parse(await readFile(this.#manifestPath, 'utf8'))
    } catch {
      throw new RequirementDraftStoreError('MANIFEST_INVALID', '本地需求草稿索引已损坏。')
    }
    if (!isRecord(value) || value['schemaVersion'] !== MANIFEST_SCHEMA_VERSION || !isRecord(value['drafts'])) {
      throw new RequirementDraftStoreError('MANIFEST_INVALID', '本地需求草稿索引版本不兼容。')
    }
    const drafts: Record<string, StoredDraft> = {}
    for (const [scopeId, rawDraft] of Object.entries(value['drafts'])) {
      assertScopeId(scopeId)
      if (!isRecord(rawDraft) || typeof rawDraft['updatedAt'] !== 'string') {
        throw new RequirementDraftStoreError('MANIFEST_INVALID', '本地需求草稿记录无效。')
      }
      const text = rawDraft['text']
      const rawAttachments = rawDraft['attachments']
      if (typeof text !== 'string' || text.length > MAX_REQUIREMENT_DRAFT_CHARS || !Array.isArray(rawAttachments) || rawAttachments.length > MAX_DRAFT_ATTACHMENTS) {
        throw new RequirementDraftStoreError('MANIFEST_INVALID', '本地需求草稿内容无效。')
      }
      drafts[scopeId] = {
        text,
        attachments: rawAttachments.map(assertDraftAttachment),
        updatedAt: rawDraft['updatedAt']
      }
    }
    return { schemaVersion: MANIFEST_SCHEMA_VERSION, drafts }
  }

  private async #persist(): Promise<void> {
    const temporaryPath = join(this.#root, `manifest-${randomUUID()}.tmp`)
    const drafts = Object.fromEntries([...this.#drafts].sort(([left], [right]) => left.localeCompare(right)))
    const serialized = `${JSON.stringify({ schemaVersion: MANIFEST_SCHEMA_VERSION, drafts }, null, 2)}\n`
    if (Buffer.byteLength(serialized, 'utf8') > MAX_MANIFEST_BYTES) {
      throw new RequirementDraftStoreError('MANIFEST_TOO_LARGE', '本地需求草稿索引超过安全上限。')
    }
    try {
      await writeFile(temporaryPath, serialized, { encoding: 'utf8', flag: 'wx', mode: 0o600 })
      try {
        await rename(temporaryPath, this.#manifestPath)
      } catch (error) {
        const code = (error as NodeJS.ErrnoException).code
        if (code !== 'EEXIST' && code !== 'EPERM') throw error
        await rm(this.#manifestPath, { force: true })
        await rename(temporaryPath, this.#manifestPath)
      }
    } finally {
      await rm(temporaryPath, { force: true })
    }
  }

  private async #removeUnreferencedAttachments(): Promise<void> {
    const referenced = new Set([...this.#drafts.values()].flatMap((draft) => draft.attachments.map((attachment) => attachment.id)))
    for (const [id, entry] of [...this.#attachments]) {
      if (referenced.has(id)) continue
      this.#attachments.delete(id)
      await rm(entry.path, { force: true })
    }
  }

  private #exclusive<T>(action: () => Promise<T>): Promise<T> {
    const operation = this.#operation.then(action, action)
    this.#operation = operation.then(() => undefined, () => undefined)
    return operation
  }
}

async function uniqueCanonicalFiles(selectedPaths: readonly string[]): Promise<readonly string[]> {
  if (selectedPaths.length > MAX_DRAFT_ATTACHMENTS) {
    throw new RequirementDraftStoreError('TOO_MANY_FILES', `一次最多选择 ${MAX_DRAFT_ATTACHMENTS} 个文件。`)
  }
  const selected: string[] = []
  const seen = new Set<string>()
  for (const selectedPath of selectedPaths) {
    if (!isAbsolute(selectedPath)) throw new RequirementDraftStoreError('FILE_PATH_INVALID', '选择的文件路径无效。')
    const selectedStat = await lstat(selectedPath)
    if (selectedStat.isSymbolicLink()) throw new RequirementDraftStoreError('FILE_SYMLINK', '不能添加符号链接文件。')
    const canonical = await realpath(selectedPath)
    const identity = process.platform === 'win32' ? canonical.toLocaleLowerCase('en-US') : canonical
    if (seen.has(identity)) continue
    seen.add(identity)
    selected.push(canonical)
  }
  return selected
}

function assertScopeId(value: string): string {
  if (value.length < 1 || value.length > 256 || !/^(?:unassigned|fixture:[a-z0-9._-]+|project:[a-f0-9]{24})$/u.test(value)) {
    throw new RequirementDraftStoreError('DRAFT_SCOPE_INVALID', '需求草稿作用域无效。')
  }
  return value
}

function assertAttachmentId(value: string): string {
  const match = /^attachment:([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$/u.exec(value)
  if (match?.[1] === undefined) throw new RequirementDraftStoreError('ATTACHMENT_ID_INVALID', '附件编号无效。')
  return match[1]
}

function assertDraftAttachment(value: unknown): DraftAttachment {
  if (!isRecord(value)) throw new RequirementDraftStoreError('ATTACHMENT_INVALID', '附件元数据无效。')
  const id = value['id']
  const name = value['name']
  const sizeBytes = value['sizeBytes']
  const sha256 = value['sha256']
  if (
    typeof id !== 'string' || typeof name !== 'string' || name.length < 1 || name.length > 512 ||
    typeof sizeBytes !== 'number' || !Number.isSafeInteger(sizeBytes) || sizeBytes < 0 || sizeBytes > MAX_DRAFT_ATTACHMENT_BYTES ||
    typeof sha256 !== 'string' || !/^[a-f0-9]{64}$/u.test(sha256)
  ) throw new RequirementDraftStoreError('ATTACHMENT_INVALID', '附件元数据无效。')
  assertAttachmentId(id)
  return { id, name, sizeBytes, sha256 }
}

function sameAttachment(left: DraftAttachment, right: DraftAttachment): boolean {
  return left.id === right.id && left.name === right.name && left.sizeBytes === right.sizeBytes && left.sha256 === right.sha256
}

function cloneDraft(draft: RequirementDraftSnapshot): RequirementDraftSnapshot {
  return { text: draft.text, attachments: draft.attachments.map((attachment) => ({ ...attachment })) }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

async function safeLstat(path: string): Promise<Awaited<ReturnType<typeof lstat>> | undefined> {
  try {
    return await lstat(path)
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return undefined
    throw error
  }
}

function sha256File(path: string): Promise<string> {
  return new Promise((resolveHash, rejectHash) => {
    const hash = createHash('sha256')
    const stream = createReadStream(path)
    stream.on('error', rejectHash)
    stream.on('data', (chunk) => hash.update(chunk))
    stream.on('end', () => resolveHash(hash.digest('hex')))
  })
}

function formatMiB(bytes: number): string {
  return `${Math.round(bytes / (1024 * 1024))} MiB`
}
