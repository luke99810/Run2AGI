import { Buffer } from 'node:buffer'
import { randomUUID } from 'node:crypto'
import { mkdir, readFile, rename, rm, writeFile } from 'node:fs/promises'
import { basename, join, resolve } from 'node:path'
import { safeStorage } from 'electron'
import type {
  AgentConfiguration,
  AgentConfigurationPatch,
  ConnectorConfiguration,
  ConnectorConfigurationInput,
  ConnectorProvider,
  McpConfiguration,
  McpConfigurationInput,
  ModelEndpoint,
  ModelEndpointPatch
} from '../../shared/protocol'
import { GLOBAL_SCOPE, ORCHESTRATOR_SCOPE } from '../../shared/protocol'

interface StoredConnector extends Omit<ConnectorConfiguration, 'credentialConfigured'> { readonly encryptedCredential?: string }
interface StoredAgent extends Omit<AgentConfiguration, 'apiKeyConfigured'> { readonly systemDocumentPath?: string; readonly encryptedApiKey?: string }
interface StoredMcp extends Omit<McpConfiguration, 'credentialConfigured'> { readonly encryptedCredential?: string }
interface ConfigurationFile {
  readonly schemaVersion: 1
  readonly connectors: readonly StoredConnector[]
  readonly agents: readonly StoredAgent[]
  readonly mcp: readonly StoredMcp[]
}

const ROLE_IDS = new Set(['intake', 'architect', 'planner', 'qa', 'coder', 'helper', 'reviewer', 'integrator', 'manager', 'evolver', 'guardian'])
const CONNECTOR_PROVIDERS = new Set<ConnectorProvider>(['custom'])
const MAX_SECRET_CHARS = 8_192
const MAX_PROMPT_CHARS = 100_000

export class WorkspaceConfigurationStore {
  readonly #directory: string
  readonly #path: string
  #connectors: StoredConnector[] = []
  #agents: StoredAgent[] = []
  #mcp: StoredMcp[] = []

  constructor(directory: string) {
    this.#directory = resolve(directory)
    this.#path = join(this.#directory, 'configuration.json')
  }

  async initialize(): Promise<void> {
    await mkdir(this.#directory, { recursive: true, mode: 0o700 })
    try {
      const value = parseFile(JSON.parse(await readFile(this.#path, 'utf8')))
      this.#connectors = [...value.connectors]
      this.#agents = [...value.agents]
      this.#mcp = [...value.mcp]
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error
      await this.#persist()
    }
  }

  listConnectors(): readonly ConnectorConfiguration[] { return this.#connectors.filter((item) => item.provider === 'custom').map(publicConnector) }
  listAgents(): readonly AgentConfiguration[] { return this.#agents.map(publicAgent) }
  listMcp(): readonly McpConfiguration[] { return this.#mcp.map(publicMcp) }

  async saveConnector(input: ConnectorConfigurationInput): Promise<ConnectorConfiguration> {
    assertConnectorInput(input)
    const id = input.id ?? `connector:${randomUUID()}`
    const current = this.#connectors.find((item) => item.id === id)
    const encryptedCredential = updateSecret(current?.encryptedCredential, input.credential, input.clearCredential)
    const next: StoredConnector = {
      id,
      provider: input.provider,
      name: input.name.trim(),
      accountLabel: input.accountLabel.trim(),
      enabled: input.enabled,
      updatedAt: new Date().toISOString(),
      ...(encryptedCredential === undefined ? {} : { encryptedCredential })
    }
    this.#connectors = [...this.#connectors.filter((item) => item.id !== id), next]
    await this.#persist()
    return publicConnector(next)
  }

  async removeConnector(id: string): Promise<boolean> {
    const next = this.#connectors.filter((item) => item.id !== id)
    if (next.length === this.#connectors.length) return false
    this.#connectors = next
    await this.#persist()
    return true
  }

  async saveAgent(roleId: string, patch: AgentConfigurationPatch): Promise<AgentConfiguration> {
    assertAgentId(roleId)
    const current = this.#agents.find((item) => item.roleId === roleId)
    assertAgentPatch(roleId, patch, current?.name)
    const encryptedApiKey = updateSecret(current?.encryptedApiKey, patch.apiKey, patch.clearApiKey)
    const endpoint = mergeEndpoint(current?.endpoint, patch.endpoint)
    const next: StoredAgent = {
      roleId,
      ...(roleId.startsWith('custom:') ? { custom: true, name: patch.name?.trim() ?? current?.name ?? '自定义 Agent' } : {}),
      systemPrompt: patch.systemPrompt ?? current?.systemPrompt ?? '',
      ...(current?.systemDocumentName === undefined ? {} : { systemDocumentName: current.systemDocumentName }),
      ...(current?.systemDocumentPath === undefined ? {} : { systemDocumentPath: current.systemDocumentPath }),
      ...(encryptedApiKey === undefined ? {} : { encryptedApiKey }),
      ...(endpoint === undefined ? {} : { endpoint }),
      updatedAt: new Date().toISOString()
    }
    this.#agents = [...this.#agents.filter((item) => item.roleId !== roleId), next]
    await this.#persist()
    return publicAgent(next)
  }

  async removeAgent(roleId: string): Promise<boolean> {
    if (!roleId.startsWith('custom:')) throw new Error('系统岗位配置不能在 C 侧删除。')
    const next = this.#agents.filter((item) => item.roleId !== roleId)
    if (next.length === this.#agents.length) return false
    this.#agents = next
    await this.#persist()
    return true
  }

  async setAgentDocument(roleId: string, path?: string): Promise<AgentConfiguration> {
    assertAgentId(roleId)
    const current = this.#agents.find((item) => item.roleId === roleId)
    const next: StoredAgent = {
      roleId,
      ...(current?.custom === true ? { custom: true, name: current.name ?? '自定义 Agent' } : {}),
      systemPrompt: current?.systemPrompt ?? '',
      ...(path === undefined ? {} : { systemDocumentName: basename(path), systemDocumentPath: resolve(path) }),
      ...(current?.encryptedApiKey === undefined ? {} : { encryptedApiKey: current.encryptedApiKey }),
      ...(current?.endpoint === undefined ? {} : { endpoint: current.endpoint }),
      updatedAt: new Date().toISOString()
    }
    this.#agents = [...this.#agents.filter((item) => item.roleId !== roleId), next]
    await this.#persist()
    return publicAgent(next)
  }

  async saveMcp(input: McpConfigurationInput): Promise<McpConfiguration> {
    assertMcpInput(input)
    const id = input.id ?? `mcp:${randomUUID()}`
    const current = this.#mcp.find((item) => item.id === id)
    const encryptedCredential = updateSecret(current?.encryptedCredential, input.credential, input.clearCredential)
    const next: StoredMcp = {
      id,
      name: input.name.trim(),
      transport: input.transport,
      endpoint: input.endpoint.trim(),
      enabled: input.enabled,
      updatedAt: new Date().toISOString(),
      ...(encryptedCredential === undefined ? {} : { encryptedCredential })
    }
    this.#mcp = [...this.#mcp.filter((item) => item.id !== id), next]
    await this.#persist()
    return publicMcp(next)
  }

  async removeMcp(id: string): Promise<boolean> {
    const next = this.#mcp.filter((item) => item.id !== id)
    if (next.length === this.#mcp.length) return false
    this.#mcp = next
    await this.#persist()
    return true
  }

  /**
   * 主进程专用：解出各 Agent 的密钥与生效的接入配置，用于**注入引擎进程**。
   *
   * ★ 这个方法是这次修复的核心。在它之前，`safeStorage.encryptString` 有
   *   （写入），而**全仓库没有任何 decryptString**（读出）—— 存进去的
   *   API Key 从来没有被读出来过。界面显示「已配置」，引擎照旧读操作系统
   *   环境变量。**一个会撒谎的已配置状态，比功能缺失难查得多。**
   *
   * ★ 刻意不经过 IPC 暴露给渲染进程：密钥解开之后只在主进程内存里存在，
   *   随即作为环境变量交给引擎子进程。渲染进程永远拿不到明文 ——
   *   那正是当初用 safeStorage 的理由，不能在补链路的时候把它拆了。
   *
   * ★ 解密失败**不抛**：换过机器或重装系统后，旧密文解不开是正常的。
   *   抛异常会让整个引擎起不来；这里跳过并交给调用方去报「需要重新填写」。
   */
  resolveSecrets(): { readonly keysByRole: Record<string, string>; readonly undecryptable: readonly string[] } {
    const keysByRole: Record<string, string> = {}
    const undecryptable: string[] = []
    if (!safeStorage.isEncryptionAvailable()) return { keysByRole, undecryptable }
    for (const agent of this.#agents) {
      if (agent.encryptedApiKey === undefined) continue
      try {
        const plain = safeStorage.decryptString(Buffer.from(agent.encryptedApiKey, 'base64'))
        if (plain.trim() !== '') keysByRole[agent.roleId] = plain
      } catch {
        undecryptable.push(agent.roleId)
      }
    }
    return { keysByRole, undecryptable }
  }

  /** 各层显式配置的接入参数，用于生成引擎读的 `model-config.json`。 */
  endpoints(): Record<string, ModelEndpoint> {
    const table: Record<string, ModelEndpoint> = {}
    for (const agent of this.#agents) {
      if (agent.endpoint !== undefined) table[agent.roleId] = agent.endpoint
    }
    return table
  }

  async #persist(): Promise<void> {
    const temporary = join(this.#directory, `configuration-${randomUUID()}.tmp`)
    const file: ConfigurationFile = { schemaVersion: 1, connectors: this.#connectors, agents: this.#agents, mcp: this.#mcp }
    await writeFile(temporary, `${JSON.stringify(file, null, 2)}\n`, { encoding: 'utf8', flag: 'wx', mode: 0o600 })
    try { await rename(temporary, this.#path) } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'EEXIST' && (error as NodeJS.ErrnoException).code !== 'EPERM') throw error
      await rm(this.#path, { force: true }); await rename(temporary, this.#path)
    }
  }
}

function encrypt(secret: string): string {
  if (!safeStorage.isEncryptionAvailable()) throw new Error('系统安全存储当前不可用，不能保存凭据。')
  return safeStorage.encryptString(secret).toString('base64')
}

/**
 * 合并这一层的模型接入配置。
 *
 * ★ 空字符串 = **取消这一层的配置**（回落到下一层），字段不传 = 不改动。
 *   这两者必须可区分：界面上把输入框清空是「取消」，而不是「无操作」——
 *   混成一个会让使用者发现自己清不掉已经配过的值。
 */
function mergeEndpoint(current?: ModelEndpoint, patch?: ModelEndpointPatch): ModelEndpoint | undefined {
  if (patch === undefined) return current
  const merged: Record<string, string> = {}
  for (const key of ['model', 'effort', 'baseUrl'] as const) {
    const incoming = patch[key]
    const existing = current?.[key]
    const value = incoming === undefined ? existing : incoming
    if (typeof value === 'string' && value.trim() !== '') merged[key] = value.trim()
  }
  return Object.keys(merged).length === 0 ? undefined : (merged as ModelEndpoint)
}

function updateSecret(current: string | undefined, next: string | undefined, clear?: boolean): string | undefined {
  if (clear === true) return undefined
  if (next === undefined || next === '') return current
  if (next.length > MAX_SECRET_CHARS) throw new Error('凭据过长。')
  return encrypt(next)
}

function publicConnector(value: StoredConnector): ConnectorConfiguration {
  const { encryptedCredential, ...visible } = value
  return { ...visible, credentialConfigured: encryptedCredential !== undefined }
}
function publicAgent(value: StoredAgent): AgentConfiguration {
  const { systemDocumentPath: _path, encryptedApiKey, ...visible } = value
  return { ...visible, apiKeyConfigured: encryptedApiKey !== undefined }
}
function publicMcp(value: StoredMcp): McpConfiguration {
  const { encryptedCredential, ...visible } = value
  return { ...visible, credentialConfigured: encryptedCredential !== undefined }
}

function assertConnectorInput(value: unknown): asserts value is ConnectorConfigurationInput {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw new TypeError('连接器配置无效。')
  const item = value as Record<string, unknown>
  if (!CONNECTOR_PROVIDERS.has(item['provider'] as ConnectorProvider) || typeof item['name'] !== 'string' || item['name'].trim().length < 1 || item['name'].length > 120 || typeof item['accountLabel'] !== 'string' || item['accountLabel'].length > 200 || typeof item['enabled'] !== 'boolean') throw new TypeError('连接器配置无效。')
}
function assertAgentPatch(roleId: string, value: AgentConfigurationPatch, currentName?: string): void {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw new TypeError('Agent 配置无效。')
  const nextName = value.name ?? currentName
  if (roleId.startsWith('custom:') && (typeof nextName !== 'string' || nextName.trim().length < 1 || nextName.length > 120)) throw new TypeError('自定义 Agent 名称无效。')
  if (value.systemPrompt !== undefined && value.systemPrompt.length > MAX_PROMPT_CHARS) throw new TypeError('系统提示词过长。')
}
function assertMcpInput(value: unknown): asserts value is McpConfigurationInput {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw new TypeError('MCP 配置无效。')
  const item = value as Record<string, unknown>
  if (typeof item['name'] !== 'string' || item['name'].trim().length < 1 || item['name'].length > 120 || !['stdio', 'http', 'sse'].includes(String(item['transport'])) || typeof item['endpoint'] !== 'string' || item['endpoint'].trim().length < 1 || item['endpoint'].length > 2_048 || typeof item['enabled'] !== 'boolean') throw new TypeError('MCP 配置无效。')
}
// ★ 两个作用域（全局 / 主 Agent）借用 roleId 字段位存放，所以它们也必须
//   通过这道校验。用保留值而不是新开一张表，是为了让「三级配置」在存储、
//   IPC、界面三处都是同一个形状 —— 三种形状意味着三次转换，也就是三处漂移点。
const SCOPE_IDS = new Set<string>([GLOBAL_SCOPE, ORCHESTRATOR_SCOPE])

function assertAgentId(roleId: string): void { if (!ROLE_IDS.has(roleId) && !SCOPE_IDS.has(roleId) && !/^custom:[0-9a-f-]{16,}$/i.test(roleId)) throw new TypeError('Agent 标识无效。') }
function parseFile(value: unknown): ConfigurationFile {
  if (typeof value !== 'object' || value === null || (value as Record<string, unknown>)['schemaVersion'] !== 1) throw new Error('配置文件格式无效。')
  const file = value as ConfigurationFile
  if (!Array.isArray(file.connectors) || !Array.isArray(file.agents) || !Array.isArray(file.mcp)) throw new Error('配置文件内容无效。')
  return file
}
