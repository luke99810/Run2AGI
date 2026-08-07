import { existsSync } from 'node:fs'
import { resolve } from 'node:path'
import { spawnSync } from 'node:child_process'
import type { App } from 'electron'
import {
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
  if (!value['connected'] && value['runId'] !== undefined) return false
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

  const script = resolve(app.getAppPath(), '..', 'delivery', 'codentum_delivery', 'sidecar.py')
  if (!existsSync(script)) throw new Error(`Development sidecar is missing: ${script}`)
  const python = findDevelopmentPython()
  if (python === undefined) throw new Error('Python 3.11+ is required only for development; packaged builds include the sidecar')
  return {
    executable: python.executable,
    args: [...python.prefix, '-X', 'utf8', '-u', script],
    cwd: resolve(app.getAppPath(), '..', '..')
  }
}

export class SidecarManager {
  readonly #app: Pick<App, 'isPackaged' | 'getAppPath'>
  #client: PythonEngineClient | undefined
  #handshake: EngineHandshake = unavailable('Sidecar has not started')
  #starting: Promise<EngineHandshake> | undefined

  public constructor(app: Pick<App, 'isPackaged' | 'getAppPath'>) {
    this.#app = app
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
      this.#client = PythonEngineClient.launch({
        ...launch,
        env: {
          ...process.env,
          PYTHONIOENCODING: 'utf-8',
          PYTHONUTF8: '1'
        }
      })
      const raw = await this.#client.request<unknown>('handshake', { protocolVersion: PROTOCOL_VERSION }, 12_000)
      if (!isHandshake(raw)) throw new Error('Sidecar returned an incompatible handshake')
      this.#handshake = raw
      return raw
    } catch (error) {
      await this.#client?.close(250).catch(() => undefined)
      this.#client = undefined
      this.#handshake = unavailable(error instanceof Error ? error.message : String(error))
      return this.#handshake
    }
  }

  public async handshake(): Promise<EngineHandshake> {
    if (this.#starting !== undefined) return this.#starting
    if (this.#client?.isRunning !== true) return this.start()
    try {
      const raw = await this.#client.request<unknown>('handshake', { protocolVersion: PROTOCOL_VERSION }, 12_000)
      if (!isHandshake(raw)) throw new Error('Sidecar returned an incompatible handshake')
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

  public async close(): Promise<void> {
    await this.#starting?.catch(() => undefined)
    await this.#client?.close()
    this.#client = undefined
    this.#handshake = unavailable('Sidecar is stopped')
  }
}
