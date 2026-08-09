import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { createInterface, type Interface as ReadLineInterface } from 'node:readline'

// The Python gateway may wait up to 8 seconds for the upstream A/B engine;
// leave transport and JSON serialization headroom outside that timeout.
const DEFAULT_REQUEST_TIMEOUT_MS = 12_000
const MAX_STDERR_CHARACTERS = 64 * 1024

export interface EngineLaunch {
  readonly executable: string
  readonly args: readonly string[]
  readonly cwd?: string
  readonly env?: NodeJS.ProcessEnv
}

interface PendingRequest {
  readonly method: string
  readonly resolve: (value: unknown) => void
  readonly reject: (error: Error) => void
  readonly timer: NodeJS.Timeout
}

type ProtocolResponse =
  | { readonly id: string; readonly ok: true; readonly result: unknown }
  | { readonly id: string; readonly ok: false; readonly error: { readonly code: string; readonly message: string } }

export class EngineTimeoutError extends Error {
  public readonly code = 'ENGINE_TIMEOUT'

  public constructor(method: string, timeoutMs: number) {
    super(`Sidecar request "${method}" timed out after ${timeoutMs}ms`)
    this.name = 'EngineTimeoutError'
  }
}

export class EngineRemoteError extends Error {
  public constructor(public readonly code: string, message: string) {
    super(message)
    this.name = 'EngineRemoteError'
  }
}

export class EngineExitedError extends Error {
  public readonly code = 'ENGINE_EXITED'

  public constructor(code: number | null, signal: NodeJS.Signals | null, stderr: string) {
    super(`Sidecar exited (code=${String(code)}, signal=${String(signal)})${stderr.trim() ? `: ${stderr.trim()}` : ''}`)
    this.name = 'EngineExitedError'
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export class PythonEngineClient {
  readonly #child: ChildProcessWithoutNullStreams
  readonly #reader: ReadLineInterface
  readonly #pending = new Map<string, PendingRequest>()
  readonly #requestTimeoutMs: number
  readonly #exitPromise: Promise<void>
  #resolveExit!: () => void
  #stderr = ''
  #sequence = 0
  #closing = false
  #exit: { readonly code: number | null; readonly signal: NodeJS.Signals | null } | undefined

  private constructor(child: ChildProcessWithoutNullStreams, requestTimeoutMs: number) {
    this.#child = child
    this.#requestTimeoutMs = requestTimeoutMs
    this.#exitPromise = new Promise((resolve) => {
      this.#resolveExit = resolve
    })
    this.#reader = createInterface({ input: child.stdout, crlfDelay: Infinity })
    this.#reader.on('line', (line) => this.#handleLine(line))
    child.stderr.setEncoding('utf8')
    child.stderr.on('data', (chunk: string) => {
      this.#stderr = `${this.#stderr}${chunk}`.slice(-MAX_STDERR_CHARACTERS)
    })
    child.once('error', (error) => this.#rejectPending(error))
    child.once('close', (code, signal) => {
      this.#exit = { code, signal }
      this.#reader.close()
      this.#rejectPending(new EngineExitedError(code, signal, this.#stderr))
      this.#resolveExit()
    })
  }

  public static launch(launch: EngineLaunch, requestTimeoutMs = DEFAULT_REQUEST_TIMEOUT_MS): PythonEngineClient {
    if (!Number.isFinite(requestTimeoutMs) || requestTimeoutMs <= 0) {
      throw new RangeError('requestTimeoutMs must be positive')
    }
    const options: Parameters<typeof spawn>[2] = {
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
      shell: false,
      detached: false
    }
    if (launch.cwd !== undefined) options.cwd = launch.cwd
    if (launch.env !== undefined) options.env = launch.env
    const child = spawn(launch.executable, [...launch.args], options) as ChildProcessWithoutNullStreams
    return new PythonEngineClient(child, requestTimeoutMs)
  }

  public get isRunning(): boolean {
    return this.#exit === undefined
  }

  public get stderr(): string {
    return this.#stderr
  }

  public request<T>(method: string, params: Readonly<Record<string, unknown>> = {}, timeoutMs = this.#requestTimeoutMs): Promise<T> {
    if (this.#exit !== undefined) {
      return Promise.reject(new EngineExitedError(this.#exit.code, this.#exit.signal, this.#stderr))
    }
    if (this.#closing && method !== 'shutdown') {
      return Promise.reject(new Error('Sidecar is closing'))
    }
    if (!method || !Number.isFinite(timeoutMs) || timeoutMs <= 0) {
      return Promise.reject(new RangeError('method and positive timeout are required'))
    }

    const id = `${process.pid}-${Date.now()}-${++this.#sequence}`
    const line = `${JSON.stringify({ id, method, params })}\n`
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.#pending.delete(id)
        reject(new EngineTimeoutError(method, timeoutMs))
      }, timeoutMs)
      this.#pending.set(id, {
        method,
        resolve: (value) => resolve(value as T),
        reject,
        timer
      })
      this.#child.stdin.write(line, 'utf8', (error) => {
        if (error === null || error === undefined) return
        const pending = this.#pending.get(id)
        if (pending === undefined) return
        clearTimeout(pending.timer)
        this.#pending.delete(id)
        pending.reject(error)
      })
    })
  }

  public async close(graceMs = 7_000): Promise<void> {
    if (this.#exit !== undefined) return
    if (!this.#closing) {
      this.#closing = true
      try {
        await this.request('shutdown', {}, Math.min(graceMs, this.#requestTimeoutMs))
      } catch {
        this.#child.stdin.end()
      }
    }
    let timer: NodeJS.Timeout | undefined
    try {
      await Promise.race([
        this.#exitPromise,
        new Promise<never>((_resolve, reject) => {
          timer = setTimeout(() => reject(new Error('Sidecar did not exit in time')), graceMs)
        })
      ])
    } catch {
      this.#child.kill()
      await this.#exitPromise
    } finally {
      if (timer !== undefined) clearTimeout(timer)
    }
  }

  #handleLine(line: string): void {
    let decoded: unknown
    try {
      decoded = JSON.parse(line)
    } catch {
      this.#rejectPending(new Error('Sidecar emitted invalid JSON on stdout'))
      return
    }
    const response = this.#parseResponse(decoded)
    if (response === undefined) {
      this.#rejectPending(new Error('Sidecar emitted an invalid response envelope'))
      return
    }
    const pending = this.#pending.get(response.id)
    if (pending === undefined) return
    clearTimeout(pending.timer)
    this.#pending.delete(response.id)
    if (response.ok) pending.resolve(response.result)
    else pending.reject(new EngineRemoteError(response.error.code, response.error.message))
  }

  #parseResponse(value: unknown): ProtocolResponse | undefined {
    if (!isRecord(value) || typeof value['id'] !== 'string' || typeof value['ok'] !== 'boolean') return undefined
    if (value['ok'] === true) return { id: value['id'], ok: true, result: value['result'] }
    const error = value['error']
    if (!isRecord(error) || typeof error['code'] !== 'string' || typeof error['message'] !== 'string') return undefined
    return { id: value['id'], ok: false, error: { code: error['code'], message: error['message'] } }
  }

  #rejectPending(error: Error): void {
    for (const pending of this.#pending.values()) {
      clearTimeout(pending.timer)
      pending.reject(error)
    }
    this.#pending.clear()
  }
}
