import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { createInterface, type Interface as ReadLineInterface } from "node:readline";

const MINIMUM_PYTHON = [3, 11] as const;
const DEFAULT_REQUEST_TIMEOUT_MS = 3_000;
const DEFAULT_STARTUP_TIMEOUT_MS = 5_000;
const MAX_STDERR_CHARACTERS = 64 * 1024;

export interface PythonCommand {
  executable: string;
  prefixArgs: readonly string[];
  version: string;
}

export interface PythonEngineLaunch {
  executable: string;
  args: readonly string[];
  cwd?: string;
  env?: NodeJS.ProcessEnv;
}

export interface PythonProbeLaunchOptions {
  scriptPath: string;
  pythonExecutable?: string;
  cwd?: string;
  env?: NodeJS.ProcessEnv;
  requestTimeoutMs?: number;
  startupTimeoutMs?: number;
}

export interface PythonEngineExit {
  code: number | null;
  signal: NodeJS.Signals | null;
}

export interface PythonProbePong {
  kind: "pong";
  protocolVersion: 1;
  pythonVersion: string;
  pid: number;
}

interface PendingRequest {
  resolve: (value: unknown) => void;
  reject: (error: Error) => void;
  timer: NodeJS.Timeout;
}

type ProtocolResponse =
  | { id: string; ok: true; result: unknown }
  | { id: string; ok: false; error: { code: string; message: string } };

export class PythonEngineTimeoutError extends Error {
  public readonly code = "PYTHON_ENGINE_TIMEOUT";

  public constructor(method: string, timeoutMs: number) {
    super(`Python engine request "${method}" timed out after ${timeoutMs} ms`);
    this.name = "PythonEngineTimeoutError";
  }
}

export class PythonEngineExitError extends Error {
  public readonly code = "PYTHON_ENGINE_EXITED";

  public constructor(exit: PythonEngineExit, stderr: string) {
    const detail = stderr.trim();
    super(`Python engine exited (code=${String(exit.code)}, signal=${String(exit.signal)})${
      detail ? `: ${detail}` : ""
    }`);
    this.name = "PythonEngineExitError";
  }
}

export class PythonEngineRemoteError extends Error {
  public constructor(public readonly code: string, message: string) {
    super(message);
    this.name = "PythonEngineRemoteError";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseVersion(version: string): readonly [number, number, number] | undefined {
  const match = /^(\d+)\.(\d+)\.(\d+)/u.exec(version.trim());
  if (match === null) {
    return undefined;
  }
  const major = Number(match[1]);
  const minor = Number(match[2]);
  const patch = Number(match[3]);
  if (![major, minor, patch].every(Number.isSafeInteger)) {
    return undefined;
  }
  return [major, minor, patch];
}

function isSupportedPython(version: string): boolean {
  const parsed = parseVersion(version);
  return parsed !== undefined
    && (parsed[0] > MINIMUM_PYTHON[0]
      || (parsed[0] === MINIMUM_PYTHON[0] && parsed[1] >= MINIMUM_PYTHON[1]));
}

function inspectPythonVersion(executable: string, prefixArgs: readonly string[]): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = spawn(
      executable,
      [...prefixArgs, "-c", "import platform; print(platform.python_version())"],
      { stdio: ["ignore", "pipe", "pipe"], windowsHide: true, shell: false },
    );
    let stdout = "";
    let stderr = "";
    let settled = false;
    const timer = setTimeout(() => {
      if (!settled) {
        settled = true;
        child.kill();
        reject(new Error(`Timed out while checking ${executable}`));
      }
    }, 3_000);

    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk: string) => {
      stderr += chunk;
    });
    child.once("error", (error) => {
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        reject(error);
      }
    });
    child.once("close", (code) => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timer);
      if (code !== 0) {
        reject(new Error(`${executable} version check failed: ${stderr.trim()}`));
        return;
      }
      const version = stdout.trim();
      if (!isSupportedPython(version)) {
        reject(new Error(`${executable} reports unsupported Python ${version || "unknown"}`));
        return;
      }
      resolve(version);
    });
  });
}

/** Resolve a Python 3.11+ interpreter without using a shell command string. */
export async function findPython311(preferredExecutable?: string): Promise<PythonCommand> {
  const candidates: ReadonlyArray<{ executable: string; prefixArgs: readonly string[] }> =
    preferredExecutable !== undefined
      ? [{ executable: preferredExecutable, prefixArgs: [] }]
      : process.platform === "win32"
        ? [
            { executable: "py", prefixArgs: ["-3.11"] },
            { executable: "py", prefixArgs: ["-3"] },
            { executable: "python", prefixArgs: [] },
            { executable: "python3.11", prefixArgs: [] },
            { executable: "python3", prefixArgs: [] },
          ]
        : [
            { executable: "python3.11", prefixArgs: [] },
            { executable: "python3", prefixArgs: [] },
            { executable: "python", prefixArgs: [] },
          ];

  const failures: string[] = [];
  for (const candidate of candidates) {
    try {
      const version = await inspectPythonVersion(candidate.executable, candidate.prefixArgs);
      return { ...candidate, version };
    } catch (error) {
      failures.push(error instanceof Error ? error.message : String(error));
    }
  }
  throw new Error(`Python 3.11+ was not found. ${failures.join("; ")}`);
}

export class PythonEngineClient {
  readonly #child: ChildProcessWithoutNullStreams;
  readonly #lineReader: ReadLineInterface;
  readonly #pending = new Map<string, PendingRequest>();
  readonly #requestTimeoutMs: number;
  readonly #exitPromise: Promise<PythonEngineExit>;
  readonly #onParentExit: () => void;
  #resolveExit!: (exit: PythonEngineExit) => void;
  #requestSequence = 0;
  #stderr = "";
  #exit: PythonEngineExit | undefined;
  #closing = false;

  private constructor(child: ChildProcessWithoutNullStreams, requestTimeoutMs: number) {
    this.#child = child;
    this.#requestTimeoutMs = requestTimeoutMs;
    this.#exitPromise = new Promise((resolve) => {
      this.#resolveExit = resolve;
    });
    this.#lineReader = createInterface({ input: child.stdout, crlfDelay: Infinity });
    this.#lineReader.on("line", (line) => {
      this.#handleLine(line);
    });
    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (chunk: string) => {
      this.#stderr = `${this.#stderr}${chunk}`.slice(-MAX_STDERR_CHARACTERS);
    });
    child.once("error", (error) => {
      this.#rejectPending(error);
    });
    child.once("close", (code, signal) => {
      const exit = { code, signal };
      this.#exit = exit;
      this.#lineReader.close();
      this.#rejectPending(new PythonEngineExitError(exit, this.#stderr));
      process.removeListener("exit", this.#onParentExit);
      this.#resolveExit(exit);
    });
    this.#onParentExit = () => {
      if (this.#exit === undefined) {
        this.#child.kill();
      }
    };
    process.once("exit", this.#onParentExit);
  }

  public static async launch(
    launch: PythonEngineLaunch,
    requestTimeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
  ): Promise<PythonEngineClient> {
    if (!Number.isFinite(requestTimeoutMs) || requestTimeoutMs <= 0) {
      throw new RangeError("requestTimeoutMs must be positive");
    }
    const spawnOptions: Parameters<typeof spawn>[2] = {
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
      shell: false,
      detached: false,
    };
    if (launch.cwd !== undefined) {
      spawnOptions.cwd = launch.cwd;
    }
    if (launch.env !== undefined) {
      spawnOptions.env = launch.env;
    }
    const child = spawn(launch.executable, [...launch.args], spawnOptions) as ChildProcessWithoutNullStreams;
    return new PythonEngineClient(child, requestTimeoutMs);
  }

  public static async launchProbe(options: PythonProbeLaunchOptions): Promise<PythonEngineClient> {
    const python = await findPython311(options.pythonExecutable);
    const launch: PythonEngineLaunch = {
      executable: python.executable,
      args: [...python.prefixArgs, "-X", "utf8", "-u", options.scriptPath],
      env: {
        ...process.env,
        ...options.env,
        PYTHONIOENCODING: "utf-8",
        PYTHONUTF8: "1",
      },
    };
    if (options.cwd !== undefined) {
      launch.cwd = options.cwd;
    }
    const client = await PythonEngineClient.launch(
      launch,
      options.requestTimeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS,
    );
    try {
      await client.ping(options.startupTimeoutMs ?? DEFAULT_STARTUP_TIMEOUT_MS);
      return client;
    } catch (error) {
      await client.terminate();
      throw error;
    }
  }

  public get pid(): number | undefined {
    return this.#child.pid;
  }

  public get isRunning(): boolean {
    return this.#exit === undefined;
  }

  public get stderr(): string {
    return this.#stderr;
  }

  public async ping(timeoutMs?: number): Promise<PythonProbePong> {
    const result = await this.request<unknown>("ping", {}, timeoutMs);
    if (
      !isRecord(result)
      || result["kind"] !== "pong"
      || result["protocolVersion"] !== 1
      || typeof result["pythonVersion"] !== "string"
      || typeof result["pid"] !== "number"
      || !isSupportedPython(result["pythonVersion"])
    ) {
      throw new Error("Python engine returned an incompatible pong response");
    }
    return result as unknown as PythonProbePong;
  }

  public request<T>(method: string, params: Record<string, unknown> = {}, timeoutMs?: number): Promise<T> {
    if (this.#exit !== undefined) {
      return Promise.reject(new PythonEngineExitError(this.#exit, this.#stderr));
    }
    if (this.#closing && method !== "shutdown") {
      return Promise.reject(new Error("Python engine is closing"));
    }
    const effectiveTimeout = timeoutMs ?? this.#requestTimeoutMs;
    if (!Number.isFinite(effectiveTimeout) || effectiveTimeout <= 0) {
      return Promise.reject(new RangeError("request timeout must be positive"));
    }
    const id = `${process.pid}-${++this.#requestSequence}`;
    const payload = `${JSON.stringify({ id, method, params })}\n`;

    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.#pending.delete(id);
        reject(new PythonEngineTimeoutError(method, effectiveTimeout));
      }, effectiveTimeout);
      this.#pending.set(id, {
        resolve: (value) => {
          resolve(value as T);
        },
        reject,
        timer,
      });
      this.#child.stdin.write(payload, "utf8", (error) => {
        if (error !== null && error !== undefined) {
          const pending = this.#pending.get(id);
          if (pending !== undefined) {
            clearTimeout(pending.timer);
            this.#pending.delete(id);
            pending.reject(error);
          }
        }
      });
    });
  }

  public async close(graceMs = 1_000): Promise<PythonEngineExit> {
    if (this.#exit !== undefined) {
      return this.#exit;
    }
    if (!Number.isFinite(graceMs) || graceMs <= 0) {
      throw new RangeError("graceMs must be positive");
    }
    if (!this.#closing) {
      this.#closing = true;
      try {
        await this.request("shutdown", {}, Math.min(graceMs, this.#requestTimeoutMs));
      } catch {
        this.#child.stdin.end();
      }
    }
    return this.#waitOrTerminate(graceMs);
  }

  public async terminate(): Promise<PythonEngineExit> {
    if (this.#exit !== undefined) {
      return this.#exit;
    }
    this.#closing = true;
    this.#child.kill();
    return this.#exitPromise;
  }

  #handleLine(line: string): void {
    let decoded: unknown;
    try {
      decoded = JSON.parse(line);
    } catch {
      this.#rejectPending(new Error("Python engine emitted invalid JSON on stdout"));
      return;
    }
    const response = this.#parseResponse(decoded);
    if (response === undefined) {
      this.#rejectPending(new Error("Python engine emitted an invalid response envelope"));
      return;
    }
    const pending = this.#pending.get(response.id);
    if (pending === undefined) {
      return;
    }
    clearTimeout(pending.timer);
    this.#pending.delete(response.id);
    if (response.ok) {
      pending.resolve(response.result);
    } else {
      pending.reject(new PythonEngineRemoteError(response.error.code, response.error.message));
    }
  }

  #parseResponse(value: unknown): ProtocolResponse | undefined {
    if (!isRecord(value) || typeof value["id"] !== "string" || typeof value["ok"] !== "boolean") {
      return undefined;
    }
    if (value["ok"] === true) {
      return { id: value["id"], ok: true, result: value["result"] };
    }
    const error = value["error"];
    if (!isRecord(error) || typeof error["code"] !== "string" || typeof error["message"] !== "string") {
      return undefined;
    }
    return {
      id: value["id"],
      ok: false,
      error: { code: error["code"], message: error["message"] },
    };
  }

  #rejectPending(error: Error): void {
    for (const pending of this.#pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.#pending.clear();
  }

  async #waitOrTerminate(graceMs: number): Promise<PythonEngineExit> {
    let timer: NodeJS.Timeout | undefined;
    try {
      return await Promise.race([
        this.#exitPromise,
        new Promise<never>((_resolve, reject) => {
          timer = setTimeout(() => reject(new Error("Python engine did not exit in time")), graceMs);
        }),
      ]);
    } catch {
      return this.terminate();
    } finally {
      if (timer !== undefined) {
        clearTimeout(timer);
      }
    }
  }
}
