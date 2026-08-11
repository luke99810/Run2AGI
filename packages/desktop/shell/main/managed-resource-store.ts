import { randomUUID } from 'node:crypto'
import { lstat, mkdir, readFile, rename, rm, stat, writeFile } from 'node:fs/promises'
import { basename, isAbsolute, join, resolve } from 'node:path'
import type {
  ManagedResource,
  ManagedResourceKind,
  ManagedResourcePatch,
  ManagedResourceScope,
  ManagedResourceSourceKind,
  OperatorCommand,
  ResourceSelection
} from '../../shared/protocol'

interface StoredResource extends ManagedResource {
  readonly localPath?: string
  readonly gitUrl?: string
}

interface RegistryFile {
  readonly schemaVersion: 1
  readonly resources: readonly StoredResource[]
}

const ROLE_IDS = new Set(['intake', 'architect', 'planner', 'qa', 'coder', 'helper', 'reviewer', 'integrator', 'manager', 'evolver', 'guardian'])
const MAX_RESOURCES = 200
const MAX_METADATA_BYTES = 256 * 1024

export class ManagedResourceStore {
  readonly #directory: string
  readonly #registryPath: string
  #resources: StoredResource[] = []

  constructor(directory: string) {
    this.#directory = resolve(directory)
    this.#registryPath = join(this.#directory, 'registry.json')
  }

  async initialize(): Promise<void> {
    await mkdir(this.#directory, { recursive: true, mode: 0o700 })
    try {
      const parsed: unknown = JSON.parse(await readFile(this.#registryPath, 'utf8'))
      this.#resources = parseRegistry(parsed)
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error
      await this.#persist()
    }
  }

  async list(kind?: ManagedResourceKind): Promise<readonly ManagedResource[]> {
    const visible = kind === undefined ? this.#resources : this.#resources.filter((resource) => resource.kind === kind)
    return Promise.all(visible.map(async (resource) => publicResource(resource, await sourceAvailable(resource))))
  }

  async addLocal(kind: ManagedResourceKind, paths: readonly string[], sourceKind: Extract<ManagedResourceSourceKind, 'file' | 'folder'>): Promise<readonly ManagedResource[]> {
    assertKind(kind)
    if (paths.length < 1 || paths.length > 20) throw new Error('一次最多添加 20 个资源。')
    if (this.#resources.length + paths.length > MAX_RESOURCES) throw new Error(`本地资源最多登记 ${MAX_RESOURCES} 项。`)
    const added: StoredResource[] = []
    for (const inputPath of paths) {
      const canonical = resolve(inputPath)
      const info = await lstat(canonical)
      if (info.isSymbolicLink()) throw new Error(`不能添加符号链接或 junction：${basename(canonical)}`)
      if (sourceKind === 'file' ? !info.isFile() : !info.isDirectory()) throw new Error(`资源类型与选择方式不一致：${basename(canonical)}`)
      const metadata = kind === 'skill' ? await readSkillMetadata(canonical, sourceKind) : undefined
      const now = new Date().toISOString()
      added.push({
        id: `managed:${randomUUID()}`,
        kind,
        name: metadata?.name ?? basename(canonical),
        description: metadata?.description ?? defaultDescription(kind, sourceKind),
        sourceKind,
        sourceLabel: basename(canonical),
        scope: kind === 'skill' ? 'role' : 'project',
        ...(kind === 'skill' ? { roleId: 'coder' } : {}),
        enabled: true,
        runtimeStatus: 'registered',
        addedAt: now,
        updatedAt: now,
        localPath: canonical
      })
    }
    const duplicate = added.find((candidate) => this.#resources.some((resource) => resource.kind === candidate.kind && resource.localPath?.toLocaleLowerCase('en-US') === candidate.localPath?.toLocaleLowerCase('en-US')))
    if (duplicate !== undefined) throw new Error(`资源已经登记：${duplicate.name}`)
    this.#resources = [...this.#resources, ...added]
    await this.#persist()
    return Promise.all(added.map(async (resource) => publicResource(resource, await sourceAvailable(resource))))
  }

  async addGit(kind: ManagedResourceKind, rawUrl: string): Promise<ManagedResource> {
    assertKind(kind)
    if (this.#resources.length >= MAX_RESOURCES) throw new Error(`本地资源最多登记 ${MAX_RESOURCES} 项。`)
    const url = new URL(rawUrl.trim())
    if (url.protocol !== 'https:' || url.username !== '' || url.password !== '' || url.search !== '' || url.hash !== '') {
      throw new Error('Git URL 必须是不含账号、密码、查询参数或片段的 HTTPS 地址。')
    }
    const sourceLabel = `${url.hostname}${url.pathname}`.replace(/\.git$/u, '')
    if (sourceLabel.length > 300) throw new Error('Git URL 过长。')
    const now = new Date().toISOString()
    const resource: StoredResource = {
      id: `managed:${randomUUID()}`,
      kind,
      name: basename(url.pathname).replace(/\.git$/u, '') || url.hostname,
      description: '已登记 Git 来源，等待运行时获取并执行准入校验。',
      sourceKind: 'git_url',
      sourceLabel,
      scope: kind === 'skill' ? 'role' : 'project',
      ...(kind === 'skill' ? { roleId: 'coder' } : {}),
      enabled: true,
      runtimeStatus: 'pending_runtime',
      addedAt: now,
      updatedAt: now,
      gitUrl: url.href
    }
    if (this.#resources.some((item) => item.kind === kind && item.gitUrl === resource.gitUrl)) throw new Error(`资源已经登记：${resource.name}`)
    this.#resources = [...this.#resources, resource]
    await this.#persist()
    return publicResource(resource, true)
  }

  async update(id: string, patch: ManagedResourcePatch): Promise<ManagedResource> {
    const index = this.#resources.findIndex((resource) => resource.id === id)
    if (index < 0) throw new Error('找不到要配置的资源。')
    const current = this.#resources[index]!
    const scope = patch.scope ?? current.scope
    assertScope(scope)
    const roleId = scope === 'role' ? patch.roleId ?? current.roleId : undefined
    if (scope === 'role' && (roleId === undefined || !ROLE_IDS.has(roleId))) throw new Error('角色作用域需要选择有效角色。')
    const next: StoredResource = {
      ...current,
      scope,
      ...(roleId === undefined ? {} : { roleId }),
      enabled: patch.enabled ?? current.enabled,
      updatedAt: new Date().toISOString()
    }
    if (scope !== 'role') delete (next as { roleId?: string }).roleId
    this.#resources = this.#resources.map((resource, at) => at === index ? next : resource)
    await this.#persist()
    return publicResource(next, await sourceAvailable(next))
  }

  async remove(id: string): Promise<boolean> {
    const next = this.#resources.filter((resource) => resource.id !== id)
    if (next.length === this.#resources.length) return false
    this.#resources = next
    await this.#persist()
    return true
  }

  async prepareCommand(command: OperatorCommand): Promise<OperatorCommand> {
    const ids = ['pluginIds', 'knowledgeIds', 'skillIds']
      .flatMap((key) => Array.isArray(command.payload[key]) ? command.payload[key] as unknown[] : [])
      .filter((value): value is string => typeof value === 'string' && value.startsWith('managed:'))
    const selected = this.#resources.filter((resource) => ids.includes(resource.id) && resource.enabled)
    const resourceSelections: ResourceSelection[] = []
    for (const resource of selected) {
      if (!await sourceAvailable(resource)) throw new Error(`资源原始位置已失效：${resource.name}`)
      resourceSelections.push({
        id: resource.id,
        kind: resource.kind,
        scope: resource.scope,
        ...(resource.roleId === undefined ? {} : { roleId: resource.roleId }),
        sourceKind: resource.sourceKind,
        ...(resource.localPath === undefined ? {} : { localPath: resource.localPath }),
        ...(resource.gitUrl === undefined ? {} : { gitUrl: resource.gitUrl })
      })
    }
    return {
      ...command,
      payload: {
        ...command.payload,
        resourceSelections,
        resourceSelectionContract: 'codentum.resource-selection.v1'
      }
    }
  }

  async #persist(): Promise<void> {
    const temporary = join(this.#directory, `registry-${randomUUID()}.tmp`)
    const payload: RegistryFile = { schemaVersion: 1, resources: this.#resources }
    await writeFile(temporary, `${JSON.stringify(payload, null, 2)}\n`, { encoding: 'utf8', flag: 'wx', mode: 0o600 })
    try {
      await rename(temporary, this.#registryPath)
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'EEXIST' && (error as NodeJS.ErrnoException).code !== 'EPERM') throw error
      await rm(this.#registryPath, { force: true })
      await rename(temporary, this.#registryPath)
    }
  }
}

function publicResource(resource: StoredResource, available: boolean): ManagedResource {
  const { localPath: _localPath, gitUrl: _gitUrl, ...visible } = resource
  return { ...visible, runtimeStatus: available ? resource.runtimeStatus : 'missing_source' }
}

async function sourceAvailable(resource: StoredResource): Promise<boolean> {
  if (resource.localPath === undefined) return true
  try { await stat(resource.localPath); return true } catch { return false }
}

async function readSkillMetadata(path: string, kind: 'file' | 'folder'): Promise<{ readonly name?: string; readonly description?: string } | undefined> {
  const manifest = kind === 'folder' ? join(path, 'SKILL.md') : path
  try {
    const info = await stat(manifest)
    if (!info.isFile() || info.size > MAX_METADATA_BYTES) return undefined
    const text = await readFile(manifest, 'utf8')
    const name = /^name:\s*["']?([^\r\n"']+)/mu.exec(text)?.[1]?.trim()
    const description = /^description:\s*["']?([^\r\n"']+)/mu.exec(text)?.[1]?.trim()
    return { ...(name === undefined ? {} : { name: name.slice(0, 120) }), ...(description === undefined ? {} : { description: description.slice(0, 300) }) }
  } catch { return undefined }
}

function defaultDescription(kind: ManagedResourceKind, sourceKind: 'file' | 'folder'): string {
  if (kind === 'skill') return `本地 ${sourceKind === 'file' ? 'Skill 文件' : 'Skill 目录'}，等待运行时准入校验。`
  if (kind === 'knowledge') return `本地知识${sourceKind === 'file' ? '文件' : '目录'}，等待 MemoryIndex 建立索引。`
  return `本地工具${sourceKind === 'file' ? '配置文件' : '目录'}，等待 ToolSurface 接入。`
}

function parseRegistry(value: unknown): StoredResource[] {
  if (typeof value !== 'object' || value === null || (value as Record<string, unknown>)['schemaVersion'] !== 1) throw new Error('资源注册表格式无效。')
  const resources = (value as Record<string, unknown>)['resources']
  if (!Array.isArray(resources) || resources.length > MAX_RESOURCES) throw new Error('资源注册表内容无效。')
  return resources.map((resource) => {
    if (typeof resource !== 'object' || resource === null) throw new Error('资源注册表条目无效。')
    const item = resource as StoredResource
    assertKind(item.kind); assertScope(item.scope)
    if (
      typeof item.id !== 'string' || !/^managed:[0-9a-f-]{36}$/u.test(item.id) ||
      typeof item.name !== 'string' || item.name.length < 1 || item.name.length > 120 ||
      typeof item.description !== 'string' || item.description.length > 300 ||
      (item.sourceKind !== 'file' && item.sourceKind !== 'folder' && item.sourceKind !== 'git_url') ||
      typeof item.sourceLabel !== 'string' || item.sourceLabel.length < 1 || item.sourceLabel.length > 300 ||
      typeof item.enabled !== 'boolean' ||
      (item.runtimeStatus !== 'registered' && item.runtimeStatus !== 'pending_runtime') ||
      Number.isNaN(Date.parse(item.addedAt)) || Number.isNaN(Date.parse(item.updatedAt)) ||
      (item.localPath !== undefined && (typeof item.localPath !== 'string' || !isAbsolute(item.localPath))) ||
      (item.gitUrl !== undefined && typeof item.gitUrl !== 'string') ||
      (item.sourceKind === 'git_url' ? item.gitUrl === undefined || item.localPath !== undefined : item.localPath === undefined || item.gitUrl !== undefined) ||
      (item.gitUrl !== undefined && !isSafeGitUrl(item.gitUrl)) ||
      (item.scope === 'role' && (item.roleId === undefined || !ROLE_IDS.has(item.roleId)))
    ) throw new Error('资源注册表条目无效。')
    return item
  })
}

function isSafeGitUrl(rawUrl: string): boolean {
  try {
    const url = new URL(rawUrl)
    return url.protocol === 'https:' && url.username === '' && url.password === '' && url.search === '' && url.hash === ''
  } catch {
    return false
  }
}

function assertKind(value: unknown): asserts value is ManagedResourceKind {
  if (value !== 'plugin' && value !== 'knowledge' && value !== 'skill') throw new Error('资源类型无效。')
}

function assertScope(value: unknown): asserts value is ManagedResourceScope {
  if (value !== 'global' && value !== 'role' && value !== 'project') throw new Error('资源作用域无效。')
}
