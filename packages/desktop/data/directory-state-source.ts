import { createHash } from 'node:crypto'
import { constants, watch as watchFs, type Dirent, type FSWatcher, type Stats } from 'node:fs'
import { lstat, open, readdir, realpath } from 'node:fs/promises'
import { isAbsolute, join, relative, resolve, sep } from 'node:path'
import type {
  BudgetFile,
  DecisionRecord,
  Evidence,
  GraphFile,
  KnowledgeFile,
  RoleSpec,
  WorkPacket
} from '@codentum/contracts'
import type {
  McpServiceProjection,
  SnapshotSourceDescriptor,
  StateSnapshot,
  WorkerEventProjection,
  WorkerProjection,
  WorkerState
} from '../shared/protocol'
import type { StateListener, StateSource } from './state-source'

const DEFAULT_POLL_INTERVAL_MS = 1_000
const DEFAULT_STALE_AFTER_MS = 2 * 60_000
const READ_ATTEMPTS = 3
const MAX_STATE_FILE_BYTES = 4 * 1024 * 1024
const MAX_STATE_FILES = 5_000
const MAX_TOTAL_STATE_BYTES = 64 * 1024 * 1024
const NO_FOLLOW_FLAG = typeof constants.O_NOFOLLOW === 'number' ? constants.O_NOFOLLOW : 0

interface FileFingerprint {
  readonly size: number
  readonly mtimeMs: number
  readonly device: number
  readonly inode: number
}

interface StateScan {
  readonly files: ReadonlyMap<string, FileFingerprint>
  readonly directories: ReadonlySet<string>
  readonly warnings: readonly string[]
}

interface ReadResult {
  readonly snapshot: StateSnapshot
  readonly coherent: boolean
}

interface ParseContext {
  readonly contents: ReadonlyMap<string, string>
  readonly directories: ReadonlySet<string>
  readonly warnings: string[]
  coherent: boolean
}

export interface DirectoryStateSourceOptions {
  readonly pollIntervalMs?: number
  readonly staleAfterMs?: number
  readonly detectStaleWorkers?: boolean
  readonly additionalWorkerStateDirectories?: () => Promise<readonly string[]>
}

/** Shared implementation for a fixture directory and a selected project. */
export abstract class DirectoryStateSource implements StateSource {
  readonly descriptor: SnapshotSourceDescriptor

  private readonly stateDirectory: string
  private readonly pollIntervalMs: number
  private readonly staleAfterMs: number
  private readonly detectStaleWorkers: boolean
  private readonly additionalWorkerStateDirectories: (() => Promise<readonly string[]>) | undefined
  private readonly listeners = new Set<StateListener>()
  private previousCoherent: StateSnapshot | undefined
  private activeRead: Promise<StateSnapshot> | undefined
  private refresh: Promise<void> | undefined
  private pollTimer: ReturnType<typeof setInterval> | undefined
  private debounceTimer: ReturnType<typeof setTimeout> | undefined
  private fsWatcher: FSWatcher | undefined
  private lastEmissionKey: string | undefined
  private closed = false

  protected constructor(
    descriptor: SnapshotSourceDescriptor,
    stateDirectory: string,
    options: DirectoryStateSourceOptions = {}
  ) {
    this.descriptor = descriptor
    this.stateDirectory = resolve(stateDirectory)
    this.pollIntervalMs = positiveInteger(options.pollIntervalMs, DEFAULT_POLL_INTERVAL_MS)
    this.staleAfterMs = positiveInteger(options.staleAfterMs, DEFAULT_STALE_AFTER_MS)
    this.detectStaleWorkers = options.detectStaleWorkers ?? true
    this.additionalWorkerStateDirectories = options.additionalWorkerStateDirectories
  }

  read(): Promise<StateSnapshot> {
    if (this.activeRead !== undefined) return this.activeRead

    const operation = this.performRead()
    this.activeRead = operation
    const clearActiveRead = (): void => {
      if (this.activeRead === operation) this.activeRead = undefined
    }
    void operation.then(clearActiveRead, clearActiveRead)
    return operation
  }

  watch(listener: StateListener): () => void {
    if (this.closed) throw new Error(`State source is closed: ${this.descriptor.id}`)

    this.listeners.add(listener)
    if (this.listeners.size === 1) this.startWatching()
    this.schedulePublish()

    return () => {
      this.listeners.delete(listener)
      if (this.listeners.size === 0) this.stopWatching()
    }
  }

  close(): void {
    if (this.closed) return
    this.closed = true
    this.listeners.clear()
    this.stopWatching()
  }

  private async performRead(): Promise<StateSnapshot> {
    const result = await readDirectorySnapshot(
      this.descriptor,
      this.stateDirectory,
      this.detectStaleWorkers ? this.staleAfterMs : undefined,
      this.additionalWorkerStateDirectories
    )

    if (result.coherent) {
      this.previousCoherent = result.snapshot
      return result.snapshot
    }

    if (this.previousCoherent === undefined) return result.snapshot

    return {
      ...this.previousCoherent,
      readAt: new Date().toISOString(),
      warnings: unique([
        ...result.snapshot.warnings,
        '[stale] The last coherent snapshot from this same source is shown; no fixture fallback was used.'
      ])
    }
  }

  private startWatching(): void {
    this.pollTimer = setInterval(() => this.schedulePublish(), this.pollIntervalMs)
    this.pollTimer.unref?.()

    try {
      this.fsWatcher = watchFs(this.stateDirectory, { recursive: true }, () => {
        if (this.debounceTimer !== undefined) clearTimeout(this.debounceTimer)
        this.debounceTimer = setTimeout(() => this.schedulePublish(), 30)
        this.debounceTimer.unref?.()
      })
      this.fsWatcher.on('error', () => {
        this.fsWatcher?.close()
        this.fsWatcher = undefined
      })
    } catch {
      // Polling remains active on platforms/filesystems without recursive watch.
    }
  }

  private stopWatching(): void {
    if (this.pollTimer !== undefined) clearInterval(this.pollTimer)
    if (this.debounceTimer !== undefined) clearTimeout(this.debounceTimer)
    this.pollTimer = undefined
    this.debounceTimer = undefined
    this.fsWatcher?.close()
    this.fsWatcher = undefined
  }

  private publish(): Promise<void> {
    if (this.closed || this.listeners.size === 0) return Promise.resolve()
    if (this.refresh !== undefined) return this.refresh

    const operation = (async () => {
      const snapshot = await this.read()
      const key = `${snapshot.revision}\n${snapshot.warnings.join('\n')}`
      if (key === this.lastEmissionKey) return
      this.lastEmissionKey = key
      for (const listener of [...this.listeners]) {
        try {
          listener(snapshot)
        } catch {
          // A renderer listener must not stop the source watcher.
        }
      }
    })()

    this.refresh = operation
    const clearRefresh = (): void => {
      if (this.refresh === operation) this.refresh = undefined
    }
    void operation.then(clearRefresh, clearRefresh)
    return operation
  }

  private schedulePublish(): void {
    void this.publish().catch(() => undefined)
  }
}

async function readDirectorySnapshot(
  source: SnapshotSourceDescriptor,
  stateDirectory: string,
  staleAfterMs: number | undefined,
  additionalWorkerStateDirectories: (() => Promise<readonly string[]>) | undefined
): Promise<ReadResult> {
  let lastScan: StateScan = { files: new Map(), directories: new Set(), warnings: [] }
  let lastContents = new Map<string, string>()
  const atomicWarnings: string[] = []
  let stable = false

  for (let attempt = 1; attempt <= READ_ATTEMPTS; attempt += 1) {
    const before = await scanStateDirectory(stateDirectory)
    const read = await readScannedFiles(stateDirectory, before.files)
    const after = await scanStateDirectory(stateDirectory)
    lastScan = after
    lastContents = read.contents

    if (sameScan(before, after) && read.failures.length === 0) {
      stable = true
      atomicWarnings.push(...before.warnings, ...after.warnings)
      break
    }

    atomicWarnings.push(
      ...before.warnings,
      ...after.warnings,
      ...read.failures,
      `[partial-write] State changed while being read (attempt ${attempt}/${READ_ATTEMPTS}).`
    )
  }

  const warnings = unique(atomicWarnings)
  const additionalWorkers = await readAdditionalWorkerState(additionalWorkerStateDirectories)
  for (const [path, content] of additionalWorkers.contents) lastContents.set(path, content)
  const combinedDirectories = new Set(lastScan.directories)
  for (const directory of additionalWorkers.directories) combinedDirectories.add(directory)
  warnings.push(...additionalWorkers.warnings)
  const context: ParseContext = {
    contents: lastContents,
    directories: combinedDirectories,
    warnings,
    coherent: stable && additionalWorkers.coherent
  }

  const graph = parseJsonFile(context, 'graph.json', isGraphFile, true)
  const budget = parseJsonFile(context, 'budget.json', isBudgetFile, true)
  const packets = parseJsonCollection(context, 'packets/', isWorkPacket, true)
  const decisions = parseJsonLinesFile(context, 'decisions.jsonl', isDecisionRecord, true)
  const evidence = parseJsonCollection(context, 'evidence/', isEvidence, true, true)
  const knowledge = parseKnowledge(context)
  const roles = parseJsonCollection(context, 'roles/', isRoleSpec, false)
  const mcpServices = parseJsonCollection(context, 'mcp/', isMcpServiceProjection, false)
  const workers = parseWorkers(context, staleAfterMs)

  checkGraphPacketCoherence(context, graph, packets)

  const revision = contentRevision(source.id, lastContents)
  return {
    coherent: context.coherent,
    snapshot: {
      source,
      revision,
      readAt: new Date().toISOString(),
      graph,
      packets,
      budget,
      decisions,
      evidence,
      knowledge,
      roles,
      mcpServices,
      workers,
      warnings: unique(context.warnings)
    }
  }
}

async function scanStateDirectory(stateDirectory: string): Promise<StateScan> {
  const files = new Map<string, FileFingerprint>()
  const directories = new Set<string>()
  const warnings: string[] = []
  let totalBytes = 0

  const stateStat = await safeLstat(stateDirectory)
  if (stateStat === undefined || !stateStat.isDirectory()) {
    warnings.push(`[missing] State directory is unavailable: ${stateDirectory}`)
    return { files, directories, warnings }
  }

  const addFile = async (relativePath: string): Promise<void> => {
    const absolutePath = join(stateDirectory, ...relativePath.split('/'))
    const fileStat = await safeLstat(absolutePath)
    if (fileStat === undefined) return
    if (fileStat.isSymbolicLink()) {
      warnings.push(`[path] Ignored symbolic link inside .codentum: ${relativePath}`)
      return
    }
    if (!fileStat.isFile()) return
    if (fileStat.size > MAX_STATE_FILE_BYTES) {
      warnings.push(`[limit] Ignored state file larger than ${MAX_STATE_FILE_BYTES} bytes: ${relativePath}`)
      return
    }
    if (files.size >= MAX_STATE_FILES) {
      warnings.push(`[limit] Ignored state file after ${MAX_STATE_FILES} files: ${relativePath}`)
      return
    }
    if (totalBytes + fileStat.size > MAX_TOTAL_STATE_BYTES) {
      warnings.push(`[limit] Ignored state file beyond ${MAX_TOTAL_STATE_BYTES} total bytes: ${relativePath}`)
      return
    }
    totalBytes += fileStat.size
    files.set(relativePath, {
      size: fileStat.size,
      mtimeMs: fileStat.mtimeMs,
      device: fileStat.dev,
      inode: fileStat.ino
    })
  }

  await addFile('graph.json')
  await addFile('budget.json')
  await addFile('decisions.jsonl')
  await addFile('knowledge.json')

  await scanFlatJsonDirectory(stateDirectory, 'packets', directories, warnings, addFile)
  await scanFlatJsonDirectory(stateDirectory, 'knowledge', directories, warnings, addFile)
  await scanFlatJsonDirectory(stateDirectory, 'roles', directories, warnings, addFile)
  await scanFlatJsonDirectory(stateDirectory, 'mcp', directories, warnings, addFile)

  const evidencePath = join(stateDirectory, 'evidence')
  const evidenceStat = await safeLstat(evidencePath)
  if (evidenceStat?.isDirectory()) {
    directories.add('evidence')
    const entries = await safeReadDirectory(evidencePath, warnings, 'evidence')
    for (const entry of entries) {
      if (entry.isSymbolicLink()) {
        warnings.push(`[path] Ignored symbolic link inside .codentum: evidence/${entry.name}`)
      } else if (entry.isFile() && entry.name.endsWith('.json')) {
        await addFile(`evidence/${entry.name}`)
      } else if (entry.isDirectory()) {
        const workerDirectory = `evidence/${entry.name}`
        await addFile(`${workerDirectory}/manifest.json`)
        await addFile(`${workerDirectory}/events.jsonl`)
        if (
          files.has(`${workerDirectory}/manifest.json`) ||
          files.has(`${workerDirectory}/events.jsonl`)
        ) {
          directories.add(workerDirectory)
        }
      }
    }
  }

  return { files, directories, warnings }
}

async function scanFlatJsonDirectory(
  stateDirectory: string,
  name: string,
  directories: Set<string>,
  warnings: string[],
  addFile: (relativePath: string) => Promise<void>
): Promise<void> {
  const directory = join(stateDirectory, name)
  const directoryStat = await safeLstat(directory)
  if (!directoryStat?.isDirectory()) return
  directories.add(name)

  const entries = await safeReadDirectory(directory, warnings, name)
  for (const entry of entries) {
    if (entry.isSymbolicLink()) {
      warnings.push(`[path] Ignored symbolic link inside .codentum: ${name}/${entry.name}`)
    } else if (entry.isFile() && entry.name.endsWith('.json')) {
      await addFile(`${name}/${entry.name}`)
    }
  }
}

async function safeReadDirectory(
  directory: string,
  warnings: string[],
  label: string
): Promise<Dirent[]> {
  try {
    return await readdir(directory, { withFileTypes: true })
  } catch (error) {
    warnings.push(`[read] Cannot enumerate ${label}: ${errorMessage(error)}`)
    return []
  }
}

async function safeLstat(path: string): Promise<Stats | undefined> {
  try {
    return await lstat(path)
  } catch {
    return undefined
  }
}

async function readScannedFiles(
  stateDirectory: string,
  files: ReadonlyMap<string, FileFingerprint>
): Promise<{ readonly contents: Map<string, string>; readonly failures: string[] }> {
  const contents = new Map<string, string>()
  const failures: string[] = []
  if (files.size === 0) return { contents, failures }

  let realStateDirectory: string
  try {
    realStateDirectory = await realpath(stateDirectory)
  } catch (error) {
    failures.push(`[read] State directory became unavailable: ${errorMessage(error)}`)
    return { contents, failures }
  }

  await Promise.all(
    [...files.entries()].map(async ([relativePath, fingerprint]) => {
      let handle: Awaited<ReturnType<typeof open>> | undefined
      try {
        const absolutePath = resolve(stateDirectory, ...relativePath.split('/'))
        if (!isWithin(stateDirectory, absolutePath)) {
          failures.push(`[path] Refused path outside .codentum: ${relativePath}`)
          return
        }
        const pathStat = await lstat(absolutePath)
        if (pathStat.isSymbolicLink() || !pathStat.isFile()) {
          failures.push(`[path] Refused non-regular state file: ${relativePath}`)
          return
        }
        const resolvedFile = await realpath(absolutePath)
        if (!isWithin(realStateDirectory, resolvedFile)) {
          failures.push(`[path] Refused resolved path outside .codentum: ${relativePath}`)
          return
        }
        handle = await open(absolutePath, constants.O_RDONLY | NO_FOLLOW_FLAG)
        const before = await handle.stat()
        if (
          !before.isFile() || before.size > MAX_STATE_FILE_BYTES ||
          before.size !== fingerprint.size || before.mtimeMs !== fingerprint.mtimeMs ||
          before.dev !== fingerprint.device || before.ino !== fingerprint.inode
        ) {
          failures.push(`[partial-write] State file changed before read: ${relativePath}`)
          return
        }
        const text = await handle.readFile({ encoding: 'utf8' })
        const after = await handle.stat()
        if (
          after.size !== before.size || after.mtimeMs !== before.mtimeMs ||
          after.dev !== before.dev || after.ino !== before.ino
        ) {
          failures.push(`[partial-write] State file changed during read: ${relativePath}`)
          return
        }
        contents.set(relativePath, text)
      } catch (error) {
        failures.push(`[partial-write] Could not read ${relativePath}: ${errorMessage(error)}`)
      } finally {
        await handle?.close().catch(() => undefined)
      }
    })
  )

  return { contents, failures }
}

async function readAdditionalWorkerState(
  provider: (() => Promise<readonly string[]>) | undefined
): Promise<{
  readonly contents: Map<string, string>
  readonly directories: Set<string>
  readonly warnings: string[]
  readonly coherent: boolean
}> {
  const contents = new Map<string, string>()
  const directories = new Set<string>()
  const warnings: string[] = []
  if (provider === undefined) return { contents, directories, warnings, coherent: true }

  let stateDirectories: readonly string[]
  try {
    stateDirectories = unique(await provider())
  } catch (error) {
    return {
      contents,
      directories,
      warnings: [`[worktree] Could not enumerate linked Worker state: ${errorMessage(error)}`],
      coherent: false
    }
  }

  let coherent = true
  for (const stateDirectory of stateDirectories) {
    const sourceId = createHash('sha256').update(resolve(stateDirectory)).digest('hex').slice(0, 12)
    let stable = false
    let lastRead = new Map<string, string>()
    const sourceWarnings: string[] = []
    for (let attempt = 1; attempt <= READ_ATTEMPTS; attempt += 1) {
      const before = await scanStateDirectory(stateDirectory)
      const beforeFiles = workerEvidenceFiles(before.files)
      const read = await readScannedFiles(stateDirectory, beforeFiles)
      const after = await scanStateDirectory(stateDirectory)
      const afterFiles = workerEvidenceFiles(after.files)
      lastRead = read.contents
      sourceWarnings.push(...before.warnings, ...after.warnings)
      if (sameFileMap(beforeFiles, afterFiles) && read.failures.length === 0) {
        stable = true
        break
      }
      sourceWarnings.push(
        ...read.failures,
        `[partial-write] Linked Worker state changed while being read (attempt ${attempt}/${READ_ATTEMPTS}).`
      )
    }
    if (!stable) coherent = false

    for (const [path, content] of lastRead) {
      const tail = path.slice('evidence/'.length)
      const workerName = tail.split('/')[0]
      if (workerName === undefined || workerName === '') continue
      const virtualDirectory = `worktree-evidence/${sourceId}/${workerName}`
      directories.add(virtualDirectory)
      contents.set(`worktree-evidence/${sourceId}/${tail}`, content)
    }
    warnings.push(...sourceWarnings.map((warning) => `[worktree:${sourceId}] ${warning}`))
  }

  return { contents, directories, warnings: unique(warnings), coherent }
}

function workerEvidenceFiles(
  files: ReadonlyMap<string, FileFingerprint>
): Map<string, FileFingerprint> {
  return new Map([...files].filter(([path]) =>
    /^evidence\/[^/]+\/(?:manifest\.json|events\.jsonl)$/u.test(path)
  ))
}

function sameFileMap(
  left: ReadonlyMap<string, FileFingerprint>,
  right: ReadonlyMap<string, FileFingerprint>
): boolean {
  if (left.size !== right.size) return false
  for (const [path, fingerprint] of left) {
    const other = right.get(path)
    if (
      other === undefined || other.size !== fingerprint.size || other.mtimeMs !== fingerprint.mtimeMs ||
      other.device !== fingerprint.device || other.inode !== fingerprint.inode
    ) return false
  }
  return true
}

function sameScan(left: StateScan, right: StateScan): boolean {
  if (left.files.size !== right.files.size) return false
  for (const [path, fingerprint] of left.files) {
    const other = right.files.get(path)
    if (
      other === undefined || other.size !== fingerprint.size || other.mtimeMs !== fingerprint.mtimeMs ||
      other.device !== fingerprint.device || other.inode !== fingerprint.inode
    ) {
      return false
    }
  }
  return true
}

function parseJsonFile<T>(
  context: ParseContext,
  path: string,
  validator: (value: unknown) => value is T,
  required: boolean
): T | null {
  const text = context.contents.get(path)
  if (text === undefined) {
    if (required) {
      context.warnings.push(`[missing] Required state file is missing: ${path}`)
      context.coherent = false
    }
    return null
  }

  const parsed = parseJson(context, path, text)
  if (parsed === undefined) return null
  if (!validator(parsed)) {
    context.warnings.push(`[schema] State file does not match its contract: ${path}`)
    context.coherent = false
    return null
  }
  return parsed
}

function parseJsonCollection<T>(
  context: ParseContext,
  prefix: string,
  validator: (value: unknown) => value is T,
  requiredDirectory: boolean,
  directChildrenOnly = false
): T[] {
  const directory = prefix.slice(0, -1)
  if (requiredDirectory && !context.directories.has(directory)) {
    context.warnings.push(`[missing] Required state directory is missing: ${directory}/`)
    context.coherent = false
  }

  const values: T[] = []
  for (const [path, text] of [...context.contents].sort(([a], [b]) => a.localeCompare(b))) {
    if (!path.startsWith(prefix) || !path.endsWith('.json')) continue
    const tail = path.slice(prefix.length)
    if (directChildrenOnly && tail.includes('/')) continue
    const parsed = parseJson(context, path, text)
    if (parsed === undefined) continue
    if (!validator(parsed)) {
      context.warnings.push(`[schema] State file does not match its contract: ${path}`)
      context.coherent = false
      continue
    }
    values.push(parsed)
  }
  return values
}

function parseJsonLinesFile<T>(
  context: ParseContext,
  path: string,
  validator: (value: unknown) => value is T,
  required: boolean
): T[] {
  const text = context.contents.get(path)
  if (text === undefined) {
    if (required) {
      context.warnings.push(`[missing] Required state file is missing: ${path}`)
      context.coherent = false
    }
    return []
  }
  return parseJsonLines(context, path, text, validator)
}

function parseJsonLines<T>(
  context: ParseContext,
  path: string,
  text: string,
  validator: (value: unknown) => value is T
): T[] {
  const values: T[] = []
  const lines = text.split(/\r?\n/u)
  const hasCompleteLastLine = text.length === 0 || /\r?\n$/u.test(text)

  lines.forEach((line, index) => {
    if (line.trim().length === 0) return
    try {
      const parsed: unknown = JSON.parse(line)
      if (!validator(parsed)) {
        context.warnings.push(`[schema] Invalid record at ${path}:${index + 1}`)
        context.coherent = false
        return
      }
      values.push(parsed)
    } catch (error) {
      const isFinalPartial = index === lines.length - 1 && !hasCompleteLastLine
      context.warnings.push(
        isFinalPartial
          ? `[partial-write] Ignored incomplete final JSONL record at ${path}:${index + 1} (bad-json).`
          : `[bad-json] Invalid JSONL record at ${path}:${index + 1}: ${errorMessage(error)}`
      )
      context.coherent = false
    }
  })

  return values
}

function parseJson(context: ParseContext, path: string, text: string): unknown | undefined {
  try {
    return JSON.parse(text) as unknown
  } catch (error) {
    const incomplete = /unexpected end|unterminated/iu.test(errorMessage(error))
    context.warnings.push(
      incomplete
        ? `[partial-write] Incomplete JSON in ${path} (bad-json): ${errorMessage(error)}`
        : `[bad-json] Invalid JSON in ${path}: ${errorMessage(error)}`
    )
    context.coherent = false
    return undefined
  }
}

function parseKnowledge(context: ParseContext): KnowledgeFile | null {
  const direct = parseJsonFile(context, 'knowledge.json', isKnowledgeFile, false)
  const parts = parseJsonCollection(context, 'knowledge/', isKnowledgeFile, false)
  const all = direct === null ? parts : [direct, ...parts]
  if (all.length === 0) return null
  return {
    schemaVersion: 1,
    knowledge: all.flatMap((part) => part.knowledge),
    provenance: all.flatMap((part) => part.provenance)
  }
}

function parseWorkers(context: ParseContext, staleAfterMs: number | undefined): WorkerProjection[] {
  const workerDirectories = [...context.directories]
    .filter((path) =>
      /^evidence\/[^/]+$/u.test(path) || /^worktree-evidence\/[^/]+\/[^/]+$/u.test(path)
    )
    .sort((a, b) => a.localeCompare(b))
  const workers = new Map<string, WorkerProjection>()

  for (const workerDirectory of workerDirectories) {
    const manifestPath = `${workerDirectory}/manifest.json`
    const eventsPath = `${workerDirectory}/events.jsonl`
    const manifestText = context.contents.get(manifestPath)
    if (manifestText === undefined) {
      context.warnings.push(`[partial-write] Worker directory has no manifest yet: ${workerDirectory}`)
      context.coherent = false
      continue
    }

    const manifest = parseJson(context, manifestPath, manifestText)
    if (!isWorkerManifest(manifest)) {
      if (manifest !== undefined) {
        context.warnings.push(`[schema] Invalid worker manifest: ${manifestPath}`)
        context.coherent = false
      }
      continue
    }

    const eventText = context.contents.get(eventsPath)
    const events =
      eventText === undefined
        ? []
        : parseJsonLines(context, eventsPath, eventText, isWorkerEvent).sort((a, b) => a.seq - b.seq)
    checkWorkerSequence(context, eventsPath, events)

    const projection = projectWorker(manifest, events)
    if (workers.has(projection.workerId)) {
      context.warnings.push(`[duplicate] Ignored duplicate Worker projection: ${projection.workerId}`)
      continue
    }
    if (staleAfterMs !== undefined && isStale(projection, staleAfterMs)) {
      const lastAt = projection.events.at(-1)?.at ?? projection.startedAt ?? manifest.created_at
      context.warnings.push(
        `[stale] Worker ${projection.workerId} is ${projection.state} but last activity was ${lastAt}.`
      )
    }
    workers.set(projection.workerId, projection)
  }

  return [...workers.values()]
}

interface WorkerManifest {
  readonly worker_id: string
  readonly packet_id: string
  readonly role: string
  readonly attempt: number
  readonly workspace: string
  readonly created_at: string
}

function projectWorker(
  manifest: WorkerManifest,
  events: readonly WorkerEventProjection[]
): WorkerProjection {
  const started = events.find((event) => event.kind === 'started')
  const finished = [...events].reverse().find((event) => event.kind === 'finished')
  const currentModule = findLastString(events, ['currentModule', 'current_module', 'moduleId', 'module_id'])
  const spentCny = projectSpentCny(events)
  const state = projectWorkerState(events, finished)

  return {
    workerId: manifest.worker_id,
    packetId: manifest.packet_id,
    role: manifest.role,
    attempt: manifest.attempt,
    state,
    ...(started === undefined ? {} : { startedAt: started.at }),
    ...(finished === undefined ? {} : { finishedAt: finished.at }),
    ...(currentModule === undefined ? {} : { currentModule }),
    ...(spentCny === undefined ? {} : { spentCny }),
    workspace: manifest.workspace,
    events
  }
}

function projectWorkerState(
  events: readonly WorkerEventProjection[],
  finished: WorkerEventProjection | undefined
): WorkerState {
  if (finished !== undefined) {
    const status = finished.payload['status']
    if (status === 'completed' || status === 'failed' || status === 'aborted') return status
    return 'unknown'
  }
  if (events.length === 0) return 'starting'
  const latestStatus = findLastString(events, ['state', 'status'])
  if (latestStatus === 'waiting' || latestStatus === 'paused' || latestStatus === 'waiting_safe_point') {
    return 'waiting'
  }
  return events.some((event) => event.kind === 'started') ? 'running' : 'starting'
}

function projectSpentCny(events: readonly WorkerEventProjection[]): number | undefined {
  let absolute: number | undefined
  let deltas = 0
  let sawDelta = false
  for (const event of events) {
    const nextAbsolute = numberField(event.payload, ['spentCny', 'spent_cny', 'totalCny', 'total_cny'])
    if (nextAbsolute !== undefined) absolute = nextAbsolute
    const delta = numberField(event.payload, ['deltaCny', 'delta_cny'])
    if (delta !== undefined) {
      deltas += delta
      sawDelta = true
    }
  }
  return absolute ?? (sawDelta ? deltas : undefined)
}

function findLastString(
  events: readonly WorkerEventProjection[],
  keys: readonly string[]
): string | undefined {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index]
    if (event === undefined) continue
    for (const key of keys) {
      const value = event.payload[key]
      if (typeof value === 'string' && value.length > 0) return value
    }
  }
  return undefined
}

function numberField(record: Readonly<Record<string, unknown>>, keys: readonly string[]): number | undefined {
  for (const key of keys) {
    const value = record[key]
    if (typeof value === 'number' && Number.isFinite(value)) return value
  }
  return undefined
}

function checkWorkerSequence(
  context: ParseContext,
  path: string,
  events: readonly WorkerEventProjection[]
): void {
  let previous = 0
  for (const event of events) {
    if (event.seq <= previous) {
      context.warnings.push(`[schema] Worker event sequence is duplicated or non-monotonic: ${path}`)
      context.coherent = false
      return
    }
    previous = event.seq
  }
}

function isStale(worker: WorkerProjection, staleAfterMs: number): boolean {
  if (worker.state !== 'running' && worker.state !== 'starting' && worker.state !== 'waiting') return false
  const timestamp = worker.events.at(-1)?.at ?? worker.startedAt
  if (timestamp === undefined) return false
  const at = Date.parse(timestamp)
  return Number.isFinite(at) && Date.now() - at > staleAfterMs
}

function checkGraphPacketCoherence(
  context: ParseContext,
  graph: GraphFile | null,
  packets: readonly WorkPacket[]
): void {
  if (graph === null) return
  const nodes = new Set<string>(graph.dependency.nodes)
  const packetIds = new Set<string>(packets.map((packet) => packet.id))
  const missingPackets = [...nodes].filter((id) => !packetIds.has(id))
  const missingNodes = [...packetIds].filter((id) => !nodes.has(id))
  if (missingPackets.length > 0 || missingNodes.length > 0) {
    context.warnings.push(
      `[partial-write] graph.json and packets/ disagree (missing packets: ${missingPackets.join(', ') || 'none'}; missing nodes: ${missingNodes.join(', ') || 'none'}).`
    )
    context.coherent = false
  }
}

function contentRevision(sourceId: string, contents: ReadonlyMap<string, string>): string {
  const hash = createHash('sha256').update(sourceId).update('\0')
  for (const [path, content] of [...contents].sort(([a], [b]) => a.localeCompare(b))) {
    hash.update(path).update('\0').update(content).update('\0')
  }
  return `sha256:${hash.digest('hex')}`
}

function isWithin(parent: string, candidate: string): boolean {
  const rel = relative(resolve(parent), resolve(candidate))
  return rel === '' || (!rel.startsWith(`..${sep}`) && rel !== '..' && !isAbsolute(rel))
}

function isRecord(value: unknown): value is Readonly<Record<string, unknown>> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isStringArray(value: unknown): value is readonly string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string')
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function isGraphFile(value: unknown): value is GraphFile {
  if (!isRecord(value) || value['schemaVersion'] !== 1) return false
  const dependency = value['dependency']
  const ownership = value['ownership']
  if (!isRecord(dependency) || !isRecord(ownership)) return false
  const edges = dependency['edges']
  const locks = ownership['locks']
  return (
    isStringArray(dependency['nodes']) &&
    Array.isArray(edges) &&
    edges.every(
      (edge) => isRecord(edge) && typeof edge['from'] === 'string' && typeof edge['to'] === 'string'
    ) &&
    Array.isArray(locks) &&
    locks.every(
      (lock) =>
        isRecord(lock) &&
        typeof lock['pathPrefix'] === 'string' &&
        typeof lock['heldBy'] === 'string' &&
        typeof lock['acquiredAt'] === 'string'
    ) &&
    Number.isInteger(ownership['version'])
  )
}

function isBudgetBase(value: unknown): value is Readonly<Record<string, unknown>> {
  return (
    isRecord(value) &&
    value['currency'] === 'CNY' &&
    isFiniteNumber(value['limitCny']) &&
    value['limitCny'] > 0 &&
    isFiniteNumber(value['spentCny']) &&
    value['spentCny'] >= 0
  )
}

function isBudgetFile(value: unknown): value is BudgetFile {
  if (!isBudgetBase(value) || value['schemaVersion'] !== 1) return false
  const byRole = value['byRole']
  const byModel = value['byModel']
  const alerts = value['alerts']
  return (
    (value['degradationChain'] === undefined || isStringArray(value['degradationChain'])) &&
    (byRole === undefined || isNumberRecord(byRole)) &&
    (byModel === undefined || isNumberRecord(byModel)) &&
    (alerts === undefined ||
      (Array.isArray(alerts) &&
        alerts.every(
          (alert) =>
            isRecord(alert) &&
            (alert['level'] === 'warn' || alert['level'] === 'hard_stop') &&
            typeof alert['at'] === 'string' &&
            typeof alert['message'] === 'string'
        )))
  )
}

function isBudgetGrant(value: unknown): boolean {
  return isBudgetBase(value) && isStringArray(value['degradationChain'])
}

function isNumberRecord(value: unknown): boolean {
  return isRecord(value) && Object.values(value).every(isFiniteNumber)
}

const PACKET_STATES = new Set([
  'pending',
  'ready',
  'running',
  'blocked',
  'review',
  'accepted',
  'rejected',
  'abandoned'
])

const ROLE_IDS = new Set([
  'intake',
  'architect',
  'planner',
  'qa',
  'coder',
  'helper',
  'reviewer',
  'integrator',
  'manager',
  'evolver',
  'guardian'
])

function isWorkPacket(value: unknown): value is WorkPacket {
  if (!isRecord(value)) return false
  const acceptance = value['acceptance']
  const provenance = value['provenance']
  return (
    typeof value['id'] === 'string' &&
    typeof value['kind'] === 'string' &&
    PACKET_STATES.has(value['state'] as string) &&
    ROLE_IDS.has(value['role'] as string) &&
    isStringArray(value['ownsPaths']) &&
    isStringArray(value['readsPaths']) &&
    isStringArray(value['deps']) &&
    isRecord(acceptance) &&
    typeof acceptance['kind'] === 'string' &&
    typeof acceptance['predicate'] === 'string' &&
    ROLE_IDS.has(acceptance['authoredBy'] as string) &&
    isBudgetGrant(value['budget']) &&
    Number.isInteger(value['attempts']) &&
    isStringArray(value['evidence']) &&
    isRecord(provenance) &&
    ROLE_IDS.has(provenance['createdBy'] as string) &&
    typeof provenance['createdAt'] === 'string'
  )
}

function isDecisionRecord(value: unknown): value is DecisionRecord {
  return (
    isRecord(value) &&
    typeof value['at'] === 'string' &&
    (ROLE_IDS.has(value['actor'] as string) || value['actor'] === 'operator') &&
    typeof value['action'] === 'string' &&
    typeof value['reasonCode'] === 'string' &&
    (value['packetId'] === undefined || typeof value['packetId'] === 'string')
  )
}

function isEvidence(value: unknown): value is Evidence {
  return (
    isRecord(value) &&
    typeof value['ref'] === 'string' &&
    typeof value['packetId'] === 'string' &&
    ROLE_IDS.has(value['role'] as string) &&
    typeof value['kind'] === 'string' &&
    (value['verdict'] === 'pass' || value['verdict'] === 'fail') &&
    isStringArray(value['artifacts']) &&
    typeof value['prevDigest'] === 'string' &&
    typeof value['digest'] === 'string' &&
    typeof value['at'] === 'string'
  )
}

function isKnowledgeFile(value: unknown): value is KnowledgeFile {
  if (!isRecord(value) || value['schemaVersion'] !== 1) return false
  const knowledge = value['knowledge']
  const provenance = value['provenance']
  return (
    Array.isArray(knowledge) &&
    knowledge.every(
      (edge) =>
        isRecord(edge) &&
        typeof edge['from'] === 'string' &&
        typeof edge['to'] === 'string' &&
        typeof edge['relation'] === 'string' &&
        isFiniteNumber(edge['confidence'])
    ) &&
    Array.isArray(provenance) &&
    provenance.every(
      (edge) =>
        isRecord(edge) &&
        typeof edge['from'] === 'string' &&
        typeof edge['to'] === 'string' &&
        typeof edge['relation'] === 'string' &&
        typeof edge['at'] === 'string'
    )
  )
}

function isRoleSpec(value: unknown): value is RoleSpec {
  return (
    isRecord(value) &&
    ROLE_IDS.has(value['id'] as string) &&
    typeof value['usesModel'] === 'boolean' &&
    isStringArray(value['writes']) &&
    isStringArray(value['reads']) &&
    isStringArray(value['tools']) &&
    Array.isArray(value['transitions'])
  )
}

function isMcpServiceProjection(value: unknown): value is McpServiceProjection {
  return (
    isRecord(value) &&
    value['schemaVersion'] === 1 &&
    typeof value['id'] === 'string' &&
    typeof value['name'] === 'string' &&
    (value['transport'] === 'stdio' || value['transport'] === 'http' || value['transport'] === 'sse') &&
    (
      value['status'] === 'connected' ||
      value['status'] === 'connecting' ||
      value['status'] === 'disconnected' ||
      value['status'] === 'error'
    ) &&
    (
      value['authentication'] === 'not_required' ||
      value['authentication'] === 'configured' ||
      value['authentication'] === 'missing' ||
      value['authentication'] === 'unknown'
    ) &&
    isStringArray(value['tools']) &&
    (value['configSource'] === undefined || typeof value['configSource'] === 'string') &&
    (value['error'] === undefined || typeof value['error'] === 'string')
  )
}

function isWorkerManifest(value: unknown): value is WorkerManifest {
  return (
    isRecord(value) &&
    typeof value['worker_id'] === 'string' &&
    typeof value['packet_id'] === 'string' &&
    typeof value['role'] === 'string' &&
    Number.isInteger(value['attempt']) &&
    typeof value['workspace'] === 'string' &&
    typeof value['created_at'] === 'string'
  )
}

function isWorkerEvent(value: unknown): value is WorkerEventProjection {
  return (
    isRecord(value) &&
    Number.isInteger(value['seq']) &&
    typeof value['kind'] === 'string' &&
    typeof value['at'] === 'string' &&
    isRecord(value['payload'])
  )
}

function positiveInteger(value: number | undefined, fallback: number): number {
  return value !== undefined && Number.isInteger(value) && value > 0 ? value : fallback
}

function unique(values: readonly string[]): string[] {
  return [...new Set(values)]
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}
