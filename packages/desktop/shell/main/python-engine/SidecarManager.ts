import { existsSync } from 'node:fs'
import { mkdir, realpath, stat, writeFile } from 'node:fs/promises'
import { dirname, isAbsolute, join, resolve } from 'node:path'
import { spawnSync } from 'node:child_process'
import type { App } from 'electron'
import {
  GLOBAL_SCOPE,
  ORCHESTRATOR_SCOPE,
  EMPTY_CAPABILITIES,
  type CommandReceipt,
  type EngineCapability,
  type EngineHandshake,
  type OperatorAction,
  type OperatorCommand
} from '../../../shared/protocol'
import { PythonEngineClient } from './PythonEngineClient'

const PROTOCOL_VERSION = 1

interface ResolvedLaunch {
  readonly executable: string
  readonly args: readonly string[]
  readonly cwd?: string
}

const ACTION_CAPABILITY: Readonly<Partial<Record<OperatorAction, EngineCapability>>> = {
  submit_requirement: 'requirements',
  confirm_plan: 'planConfirmation',
  pause_at_safe_point: 'pauseAtSafePoint',
  resume: 'resume',
  stop: 'stop',
  stop_keep_memory: 'keepMemory',
  fork_from_checkpoint: 'forkFromCheckpoint',
  append_prompt: 'appendPrompt',
  insert_module: 'insertModule'
}

function unavailable(reason: string): EngineHandshake {
  return {
    connected: false,
    protocolVersion: PROTOCOL_VERSION,
    engineVersion: 'unavailable',
    stateRevision: 0,
    capabilities: EMPTY_CAPABILITIES,
    unavailableReason: reason
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isHandshake(value: unknown): value is EngineHandshake {
  if (!isRecord(value) || value['protocolVersion'] !== PROTOCOL_VERSION) return false
  const capabilities = value['capabilities']
  const capabilityNames = Object.keys(EMPTY_CAPABILITIES) as EngineCapability[]
  if (
    typeof value['connected'] !== 'boolean' ||
    typeof value['engineVersion'] !== 'string' || value['engineVersion'].length === 0 ||
    typeof value['stateRevision'] !== 'number' || !Number.isSafeInteger(value['stateRevision']) || value['stateRevision'] < 0 ||
    !isRecord(capabilities)
  ) return false
  if (Object.keys(capabilities).length !== capabilityNames.length) return false
  if (!capabilityNames.every((key) => typeof capabilities[key] === 'boolean')) return false
  if (value['connected'] && (typeof value['runId'] !== 'string' || value['runId'].length < 1 || value['runId'].length > 256)) return false
  if (value['connected'] && (typeof value['projectRoot'] !== 'string' || !isAbsolute(value['projectRoot']) || value['projectRoot'].length > 4096)) return false
  if (!value['connected'] && value['runId'] !== undefined) return false
  if (!value['connected'] && value['projectRoot'] !== undefined) return false
  if (!value['connected'] && capabilityNames.some((key) => capabilities[key] === true)) return false
  return value['unavailableReason'] === undefined || typeof value['unavailableReason'] === 'string'
}

function isReceipt(value: unknown, commandId: string, minimumRevision: number): value is CommandReceipt {
  if (!isRecord(value)) return false
  const status = value['status']
  const revision = value['stateRevision']
  const reason = value['reason']
  return (
    value['commandId'] === commandId &&
    (status === 'accepted' || status === 'waiting_safe_point' || status === 'applied' || status === 'rejected') &&
    typeof revision === 'number' && Number.isSafeInteger(revision) && revision >= minimumRevision &&
    typeof value['receivedAt'] === 'string' && !Number.isNaN(Date.parse(value['receivedAt'])) &&
    (reason === undefined || typeof reason === 'string')
  )
}

function decodeArgs(value: string | undefined): readonly string[] {
  if (value === undefined || value.trim() === '') return []
  const decoded: unknown = JSON.parse(value)
  if (!Array.isArray(decoded) || !decoded.every((item) => typeof item === 'string')) {
    throw new Error('CODENTUM_SIDECAR_ARGS_JSON must be a JSON string array')
  }
  return decoded
}

function findDevelopmentPython(): { readonly executable: string; readonly prefix: readonly string[] } | undefined {
  const preferred = process.env['CODENTUM_PYTHON']
  const candidates = preferred !== undefined
    ? [{ executable: preferred, prefix: [] as readonly string[] }]
    : process.platform === 'win32'
      ? [
          { executable: 'py', prefix: ['-3.11'] },
          { executable: 'py', prefix: ['-3'] },
          { executable: 'python', prefix: [] }
        ]
      : [
          { executable: 'python3.11', prefix: [] },
          { executable: 'python3', prefix: [] },
          { executable: 'python', prefix: [] }
        ]
  for (const candidate of candidates) {
    const result = spawnSync(candidate.executable, [...candidate.prefix, '-c', 'import sys; raise SystemExit(sys.version_info < (3, 11))'], {
      windowsHide: true,
      shell: false,
      stdio: 'ignore'
    })
    if (result.status === 0) return candidate
  }
  return undefined
}

const SIDECAR_RELATIVE_PATH = ['packages', 'delivery', 'codentum_delivery', 'sidecar.py'] as const

/**
 * 从 `start` 逐级向上找仓库根 —— 判据是「这一层下面有 packages/delivery/.../sidecar.py」。
 *
 * ★ 原来写的是 `resolve(app.getAppPath(), '..', 'delivery', ...)`，
 *   即硬编码「getAppPath() 一定是 packages/desktop」。这个假设只在
 *   `electron-vite dev` 下成立；用构建产物启动时 getAppPath() 指向
 *   `packages/desktop/out`，于是它去找 `packages/desktop/out/delivery/...`，
 *   必然找不到，握手直接报 unavailable。
 *
 * ★ 为什么没人发现：`scripts/screenshot-smoke.mjs` 一直带
 *   `CODENTUM_ENABLE_FIXTURES=1` 跑 —— **假数据模式根本不需要 sidecar**，
 *   所以这条路径从来没被真正走过一次。
 *   （2026-08-11 第一次用真引擎启动构建产物时才撞上。）
 *
 * 向上搜索对两种布局给出同一个答案，也不再依赖「桌面端在第几层」这种
 * 会随打包配置变化的事实。
 */
function findRepoRootFrom(start: string): string | undefined {
  let current = resolve(start)
  for (;;) {
    if (existsSync(resolve(current, ...SIDECAR_RELATIVE_PATH))) return current
    const parent = dirname(current)
    if (parent === current) return undefined
    current = parent
  }
}

export function resolveSidecarLaunch(app: Pick<App, 'isPackaged' | 'getAppPath'>): ResolvedLaunch {
  const explicit = process.env['CODENTUM_SIDECAR_EXECUTABLE']
  if (explicit !== undefined && explicit.trim() !== '') {
    return { executable: explicit, args: decodeArgs(process.env['CODENTUM_SIDECAR_ARGS_JSON']) }
  }

  if (app.isPackaged) {
    const binary = process.platform === 'win32' ? 'codentum-sidecar.exe' : 'codentum-sidecar'
    const executable = resolve(process.resourcesPath, 'python', 'codentum-sidecar', binary)
    if (!existsSync(executable)) throw new Error(`Bundled sidecar is missing: ${executable}`)
    return { executable, args: [] }
  }

  const repoRoot = findRepoRootFrom(app.getAppPath())
  if (repoRoot === undefined) {
    throw new Error(
      `Development sidecar is missing: 从 ${app.getAppPath()} 逐级向上都没找到 ` +
        `${SIDECAR_RELATIVE_PATH.join('/')}`
    )
  }
  const script = resolve(repoRoot, ...SIDECAR_RELATIVE_PATH)
  const python = findDevelopmentPython()
  if (python === undefined) throw new Error('Python 3.11+ is required only for development; packaged builds include the sidecar')
  return {
    executable: python.executable,
    args: [...python.prefix, '-X', 'utf8', '-u', script],
    cwd: repoRoot
  }
}


/**
 * 模型接入配置的来源。SidecarManager 只需要这两件事，
 * 不需要知道配置是怎么存的 —— 也因此这段链路能在没有 Electron 的测试里跑。
 */
export interface ModelConfigSource {
  endpoints(): Record<string, { model?: string; effort?: string; baseUrl?: string; systemPrompt?: string }>
  resolveSecrets(): { readonly keysByRole: Record<string, string>; readonly undecryptable: readonly string[] }
  cloudSkillsCatalog(): string
}

/**
 * 某个角色专属 API Key 的环境变量名。
 *
 * ★ 必须与引擎侧 `codentum_engine.model_config.agent_key_env` 完全一致。
 *   两边各写一份拼接是危险的 —— 有测试比对它们。
 */
export function agentKeyEnvName(role: string): string {
  return `CODENTUM_AGENT_KEY__${role.toUpperCase()}`
}


/**
 * 把三级 Agent 配置（模型接入 + 系统提示词）写成引擎读的 `<项目>/.codentum/agent-config.json`，
 * 并返回要注入引擎进程的各 Agent 专属 Key 环境变量。
 *
 * ★ 做成模块级纯函数而不是私有方法：这段是整条链路上**唯一有逻辑**的部分，
 *   而私有方法只能靠测试去戳私有字段才碰得到 —— 那种测试很脆，
 *   而且会诱使人加 `xxxForTest` 方法。能直接测的东西就不要藏起来。
 *
 * ★ 配置与密钥**分两条路**走，这是刻意的：
 *   · 非密配置（model / effort / baseUrl）→ 文件，引擎每次用时重读，
 *     使用者改完下一个 packet 就生效，不必重启
 *   · 密钥 → 进程环境变量，从不以明文落盘
 *   把密钥也写进文件会让 safeStorage 那层加密变成装饰。
 */
export async function publishModelConfig(
  source: ModelConfigSource,
  projectRoot: string
): Promise<Record<string, string>> {
  const endpoints = source.endpoints()
  const { keysByRole, undecryptable } = source.resolveSecrets()
  if (undecryptable.length > 0) {
    // ★ 出声而不是静默跳过：解不开的密钥意味着使用者要重新填一次，
    //   而他看到的界面仍然显示「已配置」。不说的话他会一直等一个
    //   永远不会好的状态。
    console.warn(`[codentum] 这些 Agent 的密钥解密失败，需要重新填写：${undecryptable.join('、')}`)
  }

  const agents: Record<string, unknown> = {}
  for (const [roleId, endpoint] of Object.entries(endpoints)) {
    if (roleId === GLOBAL_SCOPE || roleId === ORCHESTRATOR_SCOPE) continue
    agents[roleId] = {
      ...endpoint,
      ...(roleId in keysByRole ? { apiKeyEnv: agentKeyEnvName(roleId) } : {})
    }
  }
  for (const roleId of Object.keys(keysByRole)) {
    if (roleId === GLOBAL_SCOPE || roleId === ORCHESTRATOR_SCOPE) continue
    // ★ 只配了 Key、没配模型的 Agent 也要进表 —— 否则引擎不知道该用哪个
    //   环境变量去取它的凭据，那把 Key 就白配了。
    if (!(roleId in agents)) agents[roleId] = { apiKeyEnv: agentKeyEnvName(roleId) }
  }

  const payload = {
    schema: 'codentum.agent-config.v1',
    updatedAt: new Date().toISOString(),
    global: endpoints[GLOBAL_SCOPE] ?? {},
    orchestrator: endpoints[ORCHESTRATOR_SCOPE] ?? {},
    agents
  }

  const directory = join(projectRoot, '.codentum')
  await mkdir(directory, { recursive: true })
  await writeFile(join(directory, 'agent-config.json'), `${JSON.stringify(payload, null, 2)}
`, {
    encoding: 'utf8',
    mode: 0o600
  })

  const env: Record<string, string> = {}
  for (const [roleId, key] of Object.entries(keysByRole)) {
    if (roleId === GLOBAL_SCOPE) {
      // ★ 全局那把 Key 走引擎本来就认的那几个变量名之一，而不是造一个新的 ——
      //   `_KEY_ENVS` 是既有约定，造新的会让命令行启动那条路径失效。
      env['DASHSCOPE_API_KEY'] = key
      continue
    }
    env[agentKeyEnvName(roleId === ORCHESTRATOR_SCOPE ? 'planner' : roleId)] = key
  }
  return env
}

export class SidecarManager {
  readonly #app: Pick<App, 'isPackaged' | 'getAppPath'>
  #client: PythonEngineClient | undefined
  #handshake: EngineHandshake = unavailable('Sidecar has not started')
  #starting: Promise<EngineHandshake> | undefined
  #projectRoot: string | undefined
  #binding: Promise<EngineHandshake> | undefined
  #modelSource: ModelConfigSource | undefined

  public constructor(app: Pick<App, 'isPackaged' | 'getAppPath'>) {
    this.#app = app
  }

  /**
   * 注入模型接入配置的来源（通常是 `WorkspaceConfigurationStore`）。
   *
   * ★ 收一个**接口**而不是直接收 store：SidecarManager 不该知道配置是怎么存的，
   *   而这两个方法恰好是它需要的全部。这样这段链路可以在没有 Electron
   *   （因而没有 safeStorage）的测试里被完整验证 —— 而它正是此前
   *   **完全没有测试覆盖**的那一段。
   */
  public useModelConfig(source: ModelConfigSource): void {
    this.#modelSource = source
  }

  public async start(): Promise<EngineHandshake> {
    if (this.#starting !== undefined) return this.#starting
    if (this.#client?.isRunning === true) return this.#handshake
    const starting = this.#startOnce()
    this.#starting = starting
    try {
      return await starting
    } finally {
      if (this.#starting === starting) this.#starting = undefined
    }
  }

  async #startOnce(): Promise<EngineHandshake> {
    try {
      const launch = resolveSidecarLaunch(this.#app)
      // ★ 每次启动前重写一次配置文件：使用者改完配置会重启引擎，
      //   而配置必须在引擎读它之前就位。
      const agentKeyEnv = await this.#publishModelConfig()
      const cloudCatalog = this.#modelSource?.cloudSkillsCatalog()?.trim() ?? ''
      this.#client = PythonEngineClient.launch({
        ...launch,
        ...(this.#projectRoot === undefined ? {} : { cwd: this.#projectRoot }),
        env: {
          ...process.env,
          PYTHONIOENCODING: 'utf-8',
          PYTHONUTF8: '1',
          ...(this.#projectRoot === undefined ? {} : { CODENTUM_PROJECT_ROOT: this.#projectRoot }),
          // ★ 各 Agent 的专属 API Key。密钥在主进程内存里解开，作为环境变量
          //   交给引擎子进程 —— 从不落盘明文，渲染进程也永远拿不到。
          //   在这一行之前，界面上存的 Key 从来没有到达过引擎。
          ...agentKeyEnv,
          // ★ 云 Skills catalog：非密配置，走环境变量交给引擎子进程。
          //   空串 = 不启用（引擎侧默认 None，离线安全）。
          ...(cloudCatalog === '' ? {} : { CODENTUM_CLOUD_SKILLS_CATALOG: cloudCatalog })
        }
      })
      const raw = await this.#client.request<unknown>('handshake', { protocolVersion: PROTOCOL_VERSION }, 12_000)
      if (!isHandshake(raw)) throw new Error('Sidecar returned an incompatible handshake')
      if (raw.connected && !this.#matchesBoundProject(raw.projectRoot)) {
        throw new Error('Agent engine did not bind the selected project')
      }
      this.#handshake = raw
      return raw
    } catch (error) {
      await this.#client?.close(250).catch(() => undefined)
      this.#client = undefined
      this.#handshake = unavailable(error instanceof Error ? error.message : String(error))
      return this.#handshake
    }
  }


  async #publishModelConfig(): Promise<Record<string, string>> {
    if (this.#modelSource === undefined || this.#projectRoot === undefined) return {}
    return publishModelConfig(this.#modelSource, this.#projectRoot)
  }

  public async handshake(): Promise<EngineHandshake> {
    if (this.#starting !== undefined) return this.#starting
    if (this.#client?.isRunning !== true) return this.start()
    try {
      const raw = await this.#client.request<unknown>('handshake', { protocolVersion: PROTOCOL_VERSION }, 12_000)
      if (!isHandshake(raw)) throw new Error('Sidecar returned an incompatible handshake')
      if (raw.connected && !this.#matchesBoundProject(raw.projectRoot)) {
        throw new Error('Agent engine is bound to a different project')
      }
      this.#handshake = raw
      return raw
    } catch (error) {
      await this.#client.close(1_000).catch(() => undefined)
      this.#client = undefined
      this.#handshake = unavailable(error instanceof Error ? error.message : String(error))
      return this.#handshake
    }
  }

  public async command(command: OperatorCommand): Promise<CommandReceipt> {
    const handshake = await this.handshake()
    if (!handshake.connected || this.#client === undefined) {
      throw new Error(handshake.unavailableReason ?? 'Agent engine is unavailable')
    }
    const capability = ACTION_CAPABILITY[command.action]
    if (capability === undefined || !handshake.capabilities[capability]) {
      throw new Error(`Engine capability is unavailable: ${capability ?? command.action}`)
    }
    const raw = await this.#client.request<unknown>('command', { command }, 12_000)
    if (!isReceipt(raw, command.commandId, handshake.stateRevision)) {
      await this.#client.close(1_000).catch(() => undefined)
      this.#client = undefined
      this.#handshake = unavailable('Sidecar returned an invalid or non-monotonic command receipt')
      throw new Error(this.#handshake.unavailableReason)
    }
    this.#handshake = { ...handshake, stateRevision: raw.stateRevision }
    return raw
  }

  public async bindProject(projectRoot: string): Promise<EngineHandshake> {
    if (this.#binding !== undefined) await this.#binding.catch(() => undefined)
    const binding = this.#bindProjectOnce(projectRoot)
    this.#binding = binding
    try {
      return await binding
    } finally {
      if (this.#binding === binding) this.#binding = undefined
    }
  }

  async #bindProjectOnce(projectRoot: string): Promise<EngineHandshake> {
    if (!isAbsolute(projectRoot)) throw new Error('Project root must be absolute')
    const canonicalRoot = await realpath(projectRoot)
    const info = await stat(canonicalRoot)
    if (!info.isDirectory()) throw new Error('Project root must be a directory')
    if (this.#projectRoot === canonicalRoot && this.#client?.isRunning === true) return this.handshake()
    await this.#stopClient(1_000)
    this.#projectRoot = canonicalRoot
    return this.start()
  }

  #matchesBoundProject(engineProjectRoot: string | undefined): boolean {
    if (this.#projectRoot === undefined) return true
    if (engineProjectRoot === undefined) return false
    const expected = resolve(this.#projectRoot)
    const actual = resolve(engineProjectRoot)
    return process.platform === 'win32' ? actual.toLowerCase() === expected.toLowerCase() : actual === expected
  }

  async #stopClient(graceMs = 1_500): Promise<void> {
    await this.#starting?.catch(() => undefined)
    await this.#client?.close(graceMs).catch(() => undefined)
    this.#client = undefined
    this.#handshake = unavailable('Sidecar is stopped')
  }

  public async close(): Promise<void> {
    await this.#binding?.catch(() => undefined)
    await this.#stopClient()
  }
}
