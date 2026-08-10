import { createHash, randomUUID } from 'node:crypto'
import { createReadStream, type Stats } from 'node:fs'
import { lstat, mkdir, readFile, readdir, realpath, rename, rm, stat, writeFile } from 'node:fs/promises'
import { basename, isAbsolute, join, relative, resolve } from 'node:path'
import {
  MAX_DRAFT_ATTACHMENTS,
  MAX_REQUIREMENT_DRAFT_CHARS,
  type DraftAttachment,
  type OperatorCommand,
  type RequirementDraftSnapshot
} from '../../shared/protocol'

export const MAX_DRAFT_ATTACHMENT_BYTES = 512 * 1024 * 1024
export const MAX_DRAFT_ATTACHMENT_TOTAL_BYTES = 1024 * 1024 * 1024
export const MAX_DRAFT_FOLDER_FILES = 10_000
export const MAX_DRAFT_FOLDER_DIRECTORIES = 10_000
export const MAX_DRAFT_FOLDER_DEPTH = 128

const MANIFEST_SCHEMA_VERSION = 3
const COPIED_MANIFEST_SCHEMA_VERSION = 2
const LEGACY_MANIFEST_SCHEMA_VERSION = 1
const MAX_MANIFEST_BYTES = 2 * 1024 * 1024
const EMPTY_DRAFT: RequirementDraftSnapshot = { text: '', attachments: [] }

type AttachmentKind = DraftAttachment['kind']

interface StoredDraft extends RequirementDraftSnapshot {
  readonly updatedAt: string
}

interface AttachmentEntry {
  readonly metadata: DraftAttachment
  readonly path: string
  readonly cleanupPath: string | null
}

interface StoredReference {
  readonly path: string
  readonly cleanupPath: string | null
}

interface StoredManifest {
  readonly schemaVersion: number
  readonly drafts: Readonly<Record<string, StoredDraft>>
  readonly references: Readonly<Record<string, StoredReference>>
}

interface FolderDigest {
  readonly sha256: string
  readonly sizeBytes: number
  readonly fileCount: number
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
    await mkdir(this.#attachmentDirectory, { recursive: true, mode: 0o700 })
    const manifest = await this.#readManifest()
    let changed = manifest.schemaVersion !== MANIFEST_SCHEMA_VERSION
    for (const [scopeId, draft] of Object.entries(manifest.drafts)) {
      const attachments: DraftAttachment[] = []
      for (const metadata of draft.attachments) {
        const reference = manifest.references[metadata.id]
        if (manifest.schemaVersion === MANIFEST_SCHEMA_VERSION && reference === undefined) {
          changed = true
          continue
        }
        const copiedPaths = this.#attachmentPaths(metadata)
        let path = reference?.path ?? copiedPaths.path
        let cleanupPath = reference?.cleanupPath ?? copiedPaths.storagePath
        let node = await safeLstat(path)
        if (reference === undefined && node === undefined && metadata.kind === 'file') {
          const legacyPath = this.#legacyAttachmentPath(metadata.id)
          const legacyNode = await safeLstat(legacyPath)
          if (legacyNode !== undefined) {
            path = legacyPath
            cleanupPath = legacyPath
            node = legacyNode
          }
        }
        const valid = node !== undefined && !node.isSymbolicLink() && (
          metadata.kind === 'file'
            ? node.isFile() && node.size === metadata.sizeBytes
            : node.isDirectory()
        )
        if (!valid) {
          changed = true
          continue
        }
        attachments.push(metadata)
        this.#attachments.set(metadata.id, { metadata, path, cleanupPath })
      }
      this.#drafts.set(scopeId, { ...draft, attachments })
    }
    if (changed) await this.#persist()
  }

  load(scopeId: string): Promise<RequirementDraftSnapshot> {
    return this.#exclusive(async () => cloneDraft(this.#drafts.get(assertScopeId(scopeId)) ?? EMPTY_DRAFT))
  }

  save(scopeId: string, draft: unknown): Promise<void> {
    return this.#exclusive(async () => {
      const scope = assertScopeId(scopeId)
      const normalized = this.#validateDraft(draft)
      this.#drafts.set(scope, { ...normalized, updatedAt: new Date().toISOString() })
      await this.#persist()
      await this.#removeUnreferencedAttachments()
    })
  }

  addFiles(scopeId: string, selectedPaths: readonly string[]): Promise<RequirementDraftSnapshot> {
    return this.#addSelections(scopeId, selectedPaths, 'file')
  }

  addFolders(scopeId: string, selectedPaths: readonly string[]): Promise<RequirementDraftSnapshot> {
    return this.#addSelections(scopeId, selectedPaths, 'folder')
  }

  #addSelections(
    scopeId: string,
    selectedPaths: readonly string[],
    kind: AttachmentKind
  ): Promise<RequirementDraftSnapshot> {
    return this.#exclusive(async () => {
      const scope = assertScopeId(scopeId)
      const current = this.#drafts.get(scope) ?? { ...EMPTY_DRAFT, updatedAt: new Date().toISOString() }
      const selected = await uniqueCanonicalSelections(selectedPaths, kind)
      if (selected.length + current.attachments.length > MAX_DRAFT_ATTACHMENTS) {
        throw new RequirementDraftStoreError(
          'TOO_MANY_FILES',
          `一项需求最多添加 ${MAX_DRAFT_ATTACHMENTS} 个文件或文件夹。`
        )
      }

      const added: AttachmentEntry[] = []
      let totalBytes = current.attachments.reduce((total, attachment) => total + attachment.sizeBytes, 0)
      for (const source of selected) {
        let entry: AttachmentEntry
        if (kind === 'file') {
          const before = await stat(source)
          if (!before.isFile()) throw new RequirementDraftStoreError('FILE_NOT_REGULAR', '只能添加普通文件。')
          if (before.size > MAX_DRAFT_ATTACHMENT_BYTES) {
            throw new RequirementDraftStoreError('FILE_TOO_LARGE', `单个文件不能超过 ${formatMiB(MAX_DRAFT_ATTACHMENT_BYTES)}。`)
          }
          if (totalBytes + before.size > MAX_DRAFT_ATTACHMENT_TOTAL_BYTES) {
            throw new RequirementDraftStoreError('FILES_TOO_LARGE', `当前草稿的附件合计不能超过 ${formatMiB(MAX_DRAFT_ATTACHMENT_TOTAL_BYTES)}。`)
          }
          entry = await this.#referenceFile(source, before)
        } else {
          entry = await this.#referenceFolder(source, MAX_DRAFT_ATTACHMENT_TOTAL_BYTES - totalBytes)
        }

        const duplicate = [...current.attachments, ...added.map((item) => item.metadata)].find((attachment) =>
          attachment.kind === entry.metadata.kind &&
          attachment.name === entry.metadata.name &&
          attachment.sha256 === entry.metadata.sha256
        )
        if (duplicate !== undefined) continue
        totalBytes += entry.metadata.sizeBytes
        added.push(entry)
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
      const attachments = requested === undefined ? [] : requested.map((value) => assertDraftAttachment(value, false))
      if (attachments.length > MAX_DRAFT_ATTACHMENTS) {
        throw new RequirementDraftStoreError('TOO_MANY_FILES', `一项需求最多添加 ${MAX_DRAFT_ATTACHMENTS} 个文件或文件夹。`)
      }

      const engineAttachments: Array<DraftAttachment & { readonly localPath: string }> = []
      for (const metadata of attachments) {
        const owned = draft.attachments.find((attachment) => attachment.id === metadata.id)
        const entry = this.#attachments.get(metadata.id)
        if (owned === undefined || entry === undefined || !sameAttachment(owned, metadata) || !sameAttachment(entry.metadata, metadata)) {
          throw new RequirementDraftStoreError('ATTACHMENT_NOT_OWNED', `草稿附件不存在或已变化：${metadata.name}`)
        }
        await verifyStoredAttachment(entry)
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

  #validateDraft(value: unknown): RequirementDraftSnapshot {
    if (!isRecord(value) || typeof value['text'] !== 'string' || value['text'].length > MAX_REQUIREMENT_DRAFT_CHARS) {
      throw new RequirementDraftStoreError('DRAFT_INVALID', `需求草稿不能超过 ${MAX_REQUIREMENT_DRAFT_CHARS} 个字符。`)
    }
    const rawAttachments = value['attachments']
    if (!Array.isArray(rawAttachments) || rawAttachments.length > MAX_DRAFT_ATTACHMENTS) {
      throw new RequirementDraftStoreError('DRAFT_INVALID', `一项需求最多添加 ${MAX_DRAFT_ATTACHMENTS} 个文件或文件夹。`)
    }
    const attachments = rawAttachments.map((attachment) => assertDraftAttachment(attachment, false))
    for (const metadata of attachments) {
      const entry = this.#attachments.get(metadata.id)
      if (entry === undefined || !sameAttachment(entry.metadata, metadata)) {
        throw new RequirementDraftStoreError('ATTACHMENT_NOT_FOUND', `附件不存在或已变化：${metadata.name}`)
      }
    }
    return { text: value['text'], attachments }
  }

  async #referenceFile(source: string, before: Stats): Promise<AttachmentEntry> {
    const id = `attachment:${randomUUID()}`
    return {
      metadata: {
        id,
        name: attachmentDisplayName(source),
        kind: 'file',
        fileCount: 1,
        sizeBytes: before.size,
        sha256: await hashStableFile(source, before)
      },
      path: source,
      cleanupPath: null
    }
  }

  async #referenceFolder(source: string, remainingBytes: number): Promise<AttachmentEntry> {
    const id = `attachment:${randomUUID()}`
    return {
      metadata: {
        id,
        name: attachmentDisplayName(source),
        kind: 'folder',
        ...await digestFolder(source, remainingBytes)
      },
      path: source,
      cleanupPath: null
    }
  }

  #attachmentPaths(metadata: DraftAttachment): { readonly path: string; readonly storagePath: string } {
    const uuid = assertAttachmentId(metadata.id)
    if (metadata.kind === 'folder') {
      const storagePath = join(this.#attachmentDirectory, `${uuid}.folder`)
      return { path: storagePath, storagePath }
    }
    const storagePath = join(this.#attachmentDirectory, `${uuid}.file`)
    return { path: join(storagePath, metadata.name), storagePath }
  }

  #legacyAttachmentPath(id: string): string {
    return join(this.#attachmentDirectory, `${assertAttachmentId(id)}.data`)
  }

  async #readManifest(): Promise<StoredManifest> {
    const manifest = await safeLstat(this.#manifestPath)
    if (manifest === undefined) return { schemaVersion: MANIFEST_SCHEMA_VERSION, drafts: {}, references: {} }
    if (manifest.isSymbolicLink() || !manifest.isFile() || manifest.size > MAX_MANIFEST_BYTES) {
      throw new RequirementDraftStoreError('MANIFEST_INVALID', '本地需求草稿索引不可读取。')
    }
    let value: unknown
    try {
      value = JSON.parse(await readFile(this.#manifestPath, 'utf8'))
    } catch {
      throw new RequirementDraftStoreError('MANIFEST_INVALID', '本地需求草稿索引已损坏。')
    }
    if (!isRecord(value) || !isRecord(value['drafts'])) {
      throw new RequirementDraftStoreError('MANIFEST_INVALID', '本地需求草稿索引版本不兼容。')
    }
    const schemaVersion = value['schemaVersion']
    if (schemaVersion !== MANIFEST_SCHEMA_VERSION && schemaVersion !== COPIED_MANIFEST_SCHEMA_VERSION && schemaVersion !== LEGACY_MANIFEST_SCHEMA_VERSION) {
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
        attachments: rawAttachments.map((attachment) => assertDraftAttachment(attachment, schemaVersion === LEGACY_MANIFEST_SCHEMA_VERSION)),
        updatedAt: rawDraft['updatedAt']
      }
    }
    const references: Record<string, StoredReference> = {}
    if (schemaVersion === MANIFEST_SCHEMA_VERSION) {
      const rawReferences = value['references']
      if (!isRecord(rawReferences)) {
        throw new RequirementDraftStoreError('MANIFEST_INVALID', '本地需求草稿附件引用无效。')
      }
      for (const [attachmentId, rawReference] of Object.entries(rawReferences)) {
        assertAttachmentId(attachmentId)
        if (!isRecord(rawReference) || typeof rawReference['path'] !== 'string' || !isAbsolute(rawReference['path'])) {
          throw new RequirementDraftStoreError('MANIFEST_INVALID', '本地需求草稿附件路径无效。')
        }
        const cleanupPath = rawReference['cleanupPath']
        if (cleanupPath !== null && (typeof cleanupPath !== 'string' || !isAbsolute(cleanupPath))) {
          throw new RequirementDraftStoreError('MANIFEST_INVALID', '本地需求草稿附件清理路径无效。')
        }
        const resolvedCleanupPath = cleanupPath === null ? null : resolve(cleanupPath)
        if (resolvedCleanupPath !== null && !isPathInside(this.#attachmentDirectory, resolvedCleanupPath)) {
          throw new RequirementDraftStoreError('MANIFEST_INVALID', '本地需求草稿附件清理路径越界。')
        }
        references[attachmentId] = { path: resolve(rawReference['path']), cleanupPath: resolvedCleanupPath }
      }
    }
    return { schemaVersion, drafts, references }
  }

  async #persist(): Promise<void> {
    const temporaryPath = join(this.#root, `manifest-${randomUUID()}.tmp`)
    const drafts = Object.fromEntries([...this.#drafts].sort(([left], [right]) => left.localeCompare(right)))
    const referenced = new Set([...this.#drafts.values()].flatMap((draft) => draft.attachments.map((attachment) => attachment.id)))
    const references = Object.fromEntries([...this.#attachments]
      .filter(([id]) => referenced.has(id))
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([id, entry]) => [id, { path: entry.path, cleanupPath: entry.cleanupPath }]))
    const serialized = `${JSON.stringify({ schemaVersion: MANIFEST_SCHEMA_VERSION, drafts, references }, null, 2)}\n`
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

  async #removeUnreferencedAttachments(): Promise<void> {
    const referenced = new Set([...this.#drafts.values()].flatMap((draft) => draft.attachments.map((attachment) => attachment.id)))
    for (const [id, entry] of [...this.#attachments]) {
      if (referenced.has(id)) continue
      this.#attachments.delete(id)
      await removeStoredAttachment(entry)
    }
  }

  #exclusive<T>(action: () => Promise<T>): Promise<T> {
    const operation = this.#operation.then(action, action)
    this.#operation = operation.then(() => undefined, () => undefined)
    return operation
  }
}

async function uniqueCanonicalSelections(
  selectedPaths: readonly string[],
  kind: AttachmentKind
): Promise<readonly string[]> {
  if (selectedPaths.length > MAX_DRAFT_ATTACHMENTS) {
    throw new RequirementDraftStoreError('TOO_MANY_FILES', `一次最多选择 ${MAX_DRAFT_ATTACHMENTS} 个文件或文件夹。`)
  }
  const selected: string[] = []
  const seen = new Set<string>()
  for (const selectedPath of selectedPaths) {
    if (!isAbsolute(selectedPath)) throw new RequirementDraftStoreError('FILE_PATH_INVALID', '选择的路径无效。')
    const selectedNode = await lstat(selectedPath)
    if (selectedNode.isSymbolicLink()) throw new RequirementDraftStoreError('FILE_SYMLINK', '不能添加符号链接或 junction。')
    if (kind === 'file' ? !selectedNode.isFile() : !selectedNode.isDirectory()) {
      throw new RequirementDraftStoreError('FILE_KIND_INVALID', kind === 'file' ? '只能添加普通文件。' : '只能添加文件夹。')
    }
    const canonical = await realpath(selectedPath)
    const canonicalNode = await lstat(canonical)
    if (canonicalNode.isSymbolicLink() || (kind === 'file' ? !canonicalNode.isFile() : !canonicalNode.isDirectory())) {
      throw new RequirementDraftStoreError('FILE_KIND_INVALID', '选择的文件或文件夹类型无效。')
    }
    const identity = process.platform === 'win32' ? canonical.toLocaleLowerCase('en-US') : canonical
    if (seen.has(identity)) continue
    seen.add(identity)
    selected.push(canonical)
  }
  return selected
}

async function verifyStoredAttachment(entry: AttachmentEntry): Promise<void> {
  const node = await safeLstat(entry.path)
  if (node === undefined) {
    throw new RequirementDraftStoreError('ATTACHMENT_CHANGED', `本地附件已移动或删除：${entry.metadata.name}`)
  }
  if (node.isSymbolicLink()) {
    throw new RequirementDraftStoreError('ATTACHMENT_CHANGED', `本地附件已变化：${entry.metadata.name}`)
  }
  if (entry.metadata.kind === 'file') {
    if (!node.isFile() || node.size !== entry.metadata.sizeBytes) {
      throw new RequirementDraftStoreError('ATTACHMENT_CHANGED', `本地附件已变化：${entry.metadata.name}`)
    }
    const actualHash = await hashStableFile(entry.path, node)
    if (actualHash !== entry.metadata.sha256) {
      throw new RequirementDraftStoreError('ATTACHMENT_CHANGED', `本地附件校验失败：${entry.metadata.name}`)
    }
    return
  }
  if (!node.isDirectory()) {
    throw new RequirementDraftStoreError('ATTACHMENT_CHANGED', `本地附件已变化：${entry.metadata.name}`)
  }
  const actual = await digestFolder(entry.path, MAX_DRAFT_ATTACHMENT_TOTAL_BYTES)
  if (
    actual.sha256 !== entry.metadata.sha256 ||
    actual.sizeBytes !== entry.metadata.sizeBytes ||
    actual.fileCount !== entry.metadata.fileCount
  ) {
    throw new RequirementDraftStoreError('ATTACHMENT_CHANGED', `本地附件校验失败：${entry.metadata.name}`)
  }
}

async function digestFolder(
  sourceRoot: string,
  maxBytes: number
): Promise<FolderDigest> {
  const treeHash = createHash('sha256')
  treeHash.update('codentum-folder-v1\0')
  let sizeBytes = 0
  let fileCount = 0
  let directoryCount = 0
  const visitedDirectories = new Set<string>()

  async function visit(
    sourceDirectory: string,
    prefix: string,
    depth: number
  ): Promise<void> {
    if (depth > MAX_DRAFT_FOLDER_DEPTH) {
      throw new RequirementDraftStoreError('FOLDER_TOO_DEEP', `文件夹层级不能超过 ${MAX_DRAFT_FOLDER_DEPTH} 层。`)
    }
    const beforeDirectory = await lstat(sourceDirectory)
    if (beforeDirectory.isSymbolicLink() || !beforeDirectory.isDirectory()) {
      throw new RequirementDraftStoreError('FOLDER_CHANGED', `文件夹结构已变化：${basename(sourceDirectory)}`)
    }
    directoryCount += 1
    if (directoryCount > MAX_DRAFT_FOLDER_DIRECTORIES) {
      throw new RequirementDraftStoreError('FOLDER_TOO_MANY_DIRECTORIES', `单个附件最多包含 ${MAX_DRAFT_FOLDER_DIRECTORIES} 个文件夹。`)
    }
    const canonicalDirectory = await realpath(sourceDirectory)
    const directoryIdentity = process.platform === 'win32'
      ? canonicalDirectory.toLocaleLowerCase('en-US')
      : canonicalDirectory
    if (visitedDirectories.has(directoryIdentity)) {
      throw new RequirementDraftStoreError('FOLDER_CYCLE', `文件夹包含循环或重复挂载：${prefix || attachmentDisplayName(sourceRoot)}`)
    }
    visitedDirectories.add(directoryIdentity)
    const entries = await readdir(sourceDirectory, { withFileTypes: true })
    entries.sort((left, right) => compareNames(left.name, right.name))
    const entryNames = entries.map((entry) => entry.name)

    for (const entry of entries) {
      const sourcePath = join(sourceDirectory, entry.name)
      const relativePath = prefix === '' ? entry.name : `${prefix}/${entry.name}`
      const node = await lstat(sourcePath)
      if (node.isSymbolicLink()) {
        throw new RequirementDraftStoreError('FOLDER_SYMLINK', `文件夹内不能包含符号链接或 junction：${relativePath}`)
      }
      if (node.isDirectory()) {
        treeHash.update(`D\0${relativePath}\0`)
        await visit(sourcePath, relativePath, depth + 1)
        continue
      }
      if (!node.isFile()) {
        throw new RequirementDraftStoreError('FOLDER_SPECIAL_FILE', `文件夹内包含不支持的特殊文件：${relativePath}`)
      }
      if (node.size > MAX_DRAFT_ATTACHMENT_BYTES) {
        throw new RequirementDraftStoreError('FILE_TOO_LARGE', `单个文件不能超过 ${formatMiB(MAX_DRAFT_ATTACHMENT_BYTES)}：${relativePath}`)
      }
      fileCount += 1
      if (fileCount > MAX_DRAFT_FOLDER_FILES) {
        throw new RequirementDraftStoreError('FOLDER_TOO_MANY_FILES', `单个文件夹最多包含 ${MAX_DRAFT_FOLDER_FILES} 个文件。`)
      }
      sizeBytes += node.size
      if (sizeBytes > maxBytes) {
        throw new RequirementDraftStoreError('FILES_TOO_LARGE', `当前草稿的附件合计不能超过 ${formatMiB(MAX_DRAFT_ATTACHMENT_TOTAL_BYTES)}。`)
      }
      const sha256 = await hashStableFile(sourcePath, node)
      treeHash.update(`F\0${relativePath}\0${node.size}\0${sha256}\0`)
    }

    const afterEntries = await readdir(sourceDirectory)
    afterEntries.sort(compareNames)
    const afterDirectory = await lstat(sourceDirectory)
    if (
      beforeDirectory.dev !== afterDirectory.dev ||
      beforeDirectory.ino !== afterDirectory.ino ||
      beforeDirectory.mtimeMs !== afterDirectory.mtimeMs ||
      entryNames.length !== afterEntries.length ||
      entryNames.some((name, index) => name !== afterEntries[index])
    ) {
      throw new RequirementDraftStoreError('FOLDER_CHANGED', `校验期间文件夹发生变化：${basename(sourceDirectory)}`)
    }
  }

  await visit(sourceRoot, '', 0)
  return { sha256: treeHash.digest('hex'), sizeBytes, fileCount }
}

async function hashStableFile(path: string, before: Stats): Promise<string> {
  const hash = createHash('sha256')
  await new Promise<void>((resolveHash, rejectHash) => {
    const stream = createReadStream(path)
    stream.on('error', rejectHash)
    stream.on('data', (chunk) => hash.update(chunk))
    stream.on('end', resolveHash)
  })
  await assertStableFile(path, before)
  return hash.digest('hex')
}

async function assertStableFile(path: string, before: Stats): Promise<void> {
  const after = await stat(path)
  if (
    before.size !== after.size || before.mtimeMs !== after.mtimeMs ||
    before.dev !== after.dev || before.ino !== after.ino
  ) {
    throw new RequirementDraftStoreError('FILE_CHANGED', `校验期间文件发生变化：${basename(path)}`)
  }
}

async function removeStoredAttachment(entry: AttachmentEntry): Promise<void> {
  if (entry.cleanupPath !== null) await rm(entry.cleanupPath, { force: true, recursive: true })
}

function assertScopeId(value: string): string {
  if (value.length < 1 || value.length > 256 || !/^(?:unassigned|fixture:[a-z0-9._-]+|project:[a-f0-9]{24})(?::task:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})?$/u.test(value)) {
    throw new RequirementDraftStoreError('DRAFT_SCOPE_INVALID', '需求草稿作用域无效。')
  }
  return value
}

function assertAttachmentId(value: string): string {
  const match = /^attachment:([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$/u.exec(value)
  if (match?.[1] === undefined) throw new RequirementDraftStoreError('ATTACHMENT_ID_INVALID', '附件编号无效。')
  return match[1]
}

function assertDraftAttachment(value: unknown, allowLegacy: boolean): DraftAttachment {
  if (!isRecord(value)) throw new RequirementDraftStoreError('ATTACHMENT_INVALID', '附件元数据无效。')
  const id = value['id']
  const name = value['name']
  const kind = allowLegacy && value['kind'] === undefined ? 'file' : value['kind']
  const fileCount = allowLegacy && value['fileCount'] === undefined ? 1 : value['fileCount']
  const sizeBytes = value['sizeBytes']
  const sha256 = value['sha256']
  if (
    typeof id !== 'string' || typeof name !== 'string' || name.length < 1 || name.length > 512 ||
    name === '.' || name === '..' || name.includes('/') || name.includes('\\') ||
    (kind !== 'file' && kind !== 'folder') ||
    typeof fileCount !== 'number' || !Number.isSafeInteger(fileCount) || fileCount < 0 || fileCount > MAX_DRAFT_FOLDER_FILES ||
    (kind === 'file' && fileCount !== 1) ||
    typeof sizeBytes !== 'number' || !Number.isSafeInteger(sizeBytes) || sizeBytes < 0 || sizeBytes > MAX_DRAFT_ATTACHMENT_TOTAL_BYTES ||
    typeof sha256 !== 'string' || !/^[a-f0-9]{64}$/u.test(sha256)
  ) throw new RequirementDraftStoreError('ATTACHMENT_INVALID', '附件元数据无效。')
  assertAttachmentId(id)
  return { id, name, kind, fileCount, sizeBytes, sha256 }
}

function sameAttachment(left: DraftAttachment, right: DraftAttachment): boolean {
  return left.id === right.id &&
    left.name === right.name &&
    left.kind === right.kind &&
    left.fileCount === right.fileCount &&
    left.sizeBytes === right.sizeBytes &&
    left.sha256 === right.sha256
}

function cloneDraft(draft: RequirementDraftSnapshot): RequirementDraftSnapshot {
  return { text: draft.text, attachments: draft.attachments.map((attachment) => ({ ...attachment })) }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isPathInside(parent: string, candidate: string): boolean {
  const pathFromParent = relative(resolve(parent), resolve(candidate))
  return pathFromParent !== '' && pathFromParent !== '..' && !pathFromParent.startsWith(`..\\`) && !pathFromParent.startsWith('../') && !isAbsolute(pathFromParent)
}

async function safeLstat(path: string): Promise<Stats | undefined> {
  try {
    return await lstat(path) as Stats
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return undefined
    throw error
  }
}

function formatMiB(bytes: number): string {
  return `${Math.round(bytes / (1024 * 1024))} MiB`
}

function attachmentDisplayName(path: string): string {
  const name = basename(path)
  if (name !== '') return name
  const rootName = path.replace(/[:\\/]+/gu, '').trim()
  return rootName === '' ? 'root' : rootName
}

function compareNames(left: string, right: string): number {
  if (left < right) return -1
  if (left > right) return 1
  return 0
}
