import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { copyFile, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { ModuleKind, ModuleResolutionKind, ScriptTarget, transpileModule } from "typescript";
import { afterEach, describe, expect, it } from "vitest";
import {
  PythonEngineClient,
  PythonEngineRemoteError,
  PythonEngineTimeoutError,
} from "./PythonEngineClient";

const currentDirectory = dirname(fileURLToPath(import.meta.url));
const repositoryRoot = resolve(currentDirectory, "../../../../..");
const sourceProbePath = resolve(
  repositoryRoot,
  "packages/delivery/codentum_delivery/packaging/python_engine_probe.py",
);
const temporaryDirectories: string[] = [];

async function makeSpacedProbeDirectory(): Promise<{ directory: string; scriptPath: string }> {
  const root = await mkdtemp(join(tmpdir(), "codentum python engine "));
  temporaryDirectories.push(root);
  const directory = join(root, "sidecar files with spaces");
  await mkdir(directory, { recursive: true });
  const scriptPath = join(directory, "python engine probe.py");
  await copyFile(sourceProbePath, scriptPath);
  return { directory, scriptPath };
}

function runProcess(
  executable: string,
  args: readonly string[],
  env: NodeJS.ProcessEnv,
  timeoutMs: number,
): Promise<{ code: number | null; stdout: string; stderr: string }> {
  return new Promise((resolveProcess, reject) => {
    const child = spawn(executable, [...args], {
      env,
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
      shell: false,
    });
    let stdout = "";
    let stderr = "";
    let settled = false;
    const timer = setTimeout(() => {
      if (!settled) {
        settled = true;
        child.kill();
        reject(new Error(`Process timed out after ${timeoutMs} ms. stderr: ${stderr}`));
      }
    }, timeoutMs);
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
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        resolveProcess({ code, stdout, stderr });
      }
    });
  });
}

afterEach(async () => {
  await Promise.all(temporaryDirectories.splice(0).map(async (directory) => {
    await rm(directory, { recursive: true, force: true });
  }));
});

describe("PythonEngineClient JSON Lines transport", () => {
  it("pings Python 3.11+, captures stderr, times out, recovers, and exits cleanly", async () => {
    const { directory, scriptPath } = await makeSpacedProbeDirectory();
    const client = await PythonEngineClient.launchProbe({
      scriptPath,
      cwd: directory,
      requestTimeoutMs: 500,
    });

    try {
      const pong = await client.ping();
      expect(pong).toMatchObject({ kind: "pong", protocolVersion: 1 });
      expect(pong.pythonVersion).toMatch(/^3\.(?:1[1-9]|[2-9]\d)\./u);
      expect(pong.pid).toBe(client.pid);

      const unicodeValue = { prompt: "请继续执行：成本分析 🚀", result: "完成 ✅" };
      await expect(client.request("probe.echo", { value: unicodeValue }))
        .resolves.toEqual({ value: unicodeValue });

      await expect(client.request("missing.method")).rejects.toBeInstanceOf(PythonEngineRemoteError);
      await expect(client.request("probe.delay", {
        delayMs: 140,
        emitStderr: true,
      }, 25)).rejects.toBeInstanceOf(PythonEngineTimeoutError);

      await new Promise((resolveDelay) => setTimeout(resolveDelay, 170));
      await expect(client.ping()).resolves.toMatchObject({ kind: "pong" });
      expect(client.stderr).toContain("delay diagnostic");
    } finally {
      const exit = await client.close();
      expect(exit).toEqual({ code: 0, signal: null });
      expect(client.isRunning).toBe(false);
    }
  });

  it.runIf(process.platform !== "linux" || Boolean(process.env["DISPLAY"] ?? process.env["WAYLAND_DISPLAY"]))(
    "runs the same probe from a real Electron main process",
    async () => {
      const { directory, scriptPath } = await makeSpacedProbeDirectory();
      const sourcePath = resolve(currentDirectory, "PythonEngineClient.ts");
      const clientSource = await readFile(sourcePath, "utf8");
      const compiledClientPath = join(directory, "Python Engine Client.cjs");
      const runnerPath = join(directory, "Electron Main Probe.cjs");
      const compiled = transpileModule(clientSource, {
        compilerOptions: {
          target: ScriptTarget.ES2022,
          module: ModuleKind.CommonJS,
          moduleResolution: ModuleResolutionKind.Node10,
        },
        fileName: sourcePath,
        reportDiagnostics: true,
      });
      expect(compiled.diagnostics ?? []).toEqual([]);
      await writeFile(compiledClientPath, compiled.outputText, "utf8");
      await writeFile(runnerPath, String.raw`
const { app } = require("electron");
const { PythonEngineClient } = require(process.env.CODENTUM_PROBE_CLIENT_PATH);

app.whenReady().then(async () => {
  let client;
  try {
    const packagedEnginePath = process.env.CODENTUM_PACKAGED_ENGINE_PATH;
    const packagedEngine = Boolean(packagedEnginePath);
    client = packagedEngine
      ? await PythonEngineClient.launch({
          executable: packagedEnginePath,
          args: [],
          cwd: process.env.CODENTUM_PROBE_CWD,
          env: {
            ...process.env,
            PATH: "",
            PYTHONIOENCODING: "utf-8",
            PYTHONUTF8: "1",
          },
        }, 15000)
      : await PythonEngineClient.launchProbe({
          scriptPath: process.env.CODENTUM_PROBE_SCRIPT_PATH,
          cwd: process.env.CODENTUM_PROBE_CWD,
          requestTimeoutMs: 500,
        });
    const firstPong = await client.ping(15000);
    const unicodeValue = { prompt: "请继续执行：成本分析 🚀", result: "完成 ✅" };
    const unicodeEcho = await client.request("probe.echo", { value: unicodeValue });
    let timeoutCode = "none";
    try {
      await client.request("probe.delay", { delayMs: 140, emitStderr: true }, 25);
    } catch (error) {
      timeoutCode = error && error.code ? error.code : "unexpected";
    }
    await new Promise((resolveDelay) => setTimeout(resolveDelay, 170));
    const recoveredPong = await client.ping();
    const capturedStderr = client.stderr.includes("delay diagnostic");
    const exit = await client.close();
    const result = {
      electronVersion: process.versions.electron,
      nodeVersion: process.versions.node,
      pythonVersion: firstPong.pythonVersion,
      protocolVersion: recoveredPong.protocolVersion,
      timeoutCode,
      capturedStderr,
      cleanExit: exit.code === 0 && exit.signal === null && !client.isRunning,
      pathWithSpaces: packagedEngine
        ? packagedEnginePath.includes(" ")
        : process.env.CODENTUM_PROBE_SCRIPT_PATH.includes(" "),
      unicodeRoundTrip: JSON.stringify(unicodeEcho.value) === JSON.stringify(unicodeValue),
      packagedEngine,
      pythonPathIsolated: packagedEngine,
    };
    process.stdout.write("CODENTUM_ELECTRON_PYTHON_PROBE=" + JSON.stringify(result) + "\n");
    app.exit(0);
  } catch (error) {
    if (client && client.isRunning) {
      await client.terminate();
    }
    process.stderr.write(error && error.stack ? error.stack : String(error));
    app.exit(1);
  }
});
`, "utf8");

      const nodeRequire = createRequire(import.meta.url);
      const electronValue: unknown = nodeRequire("electron");
      expect(typeof electronValue).toBe("string");
      if (typeof electronValue !== "string") {
        throw new TypeError("The electron package did not resolve to an executable path");
      }
      const env: NodeJS.ProcessEnv = {
        ...process.env,
        CODENTUM_PROBE_CLIENT_PATH: compiledClientPath,
        CODENTUM_PROBE_SCRIPT_PATH: scriptPath,
        CODENTUM_PROBE_CWD: directory,
      };
      delete env["ELECTRON_RUN_AS_NODE"];
      const args = process.platform === "linux" ? ["--no-sandbox", runnerPath] : [runnerPath];
      const execution = await runProcess(
        electronValue,
        args,
        env,
        process.env["CODENTUM_PACKAGED_ENGINE_PATH"] === undefined ? 15_000 : 35_000,
      );
      expect(execution.code, execution.stderr).toBe(0);

      const marker = "CODENTUM_ELECTRON_PYTHON_PROBE=";
      const resultLine = execution.stdout.split(/\r?\n/u).find((line) => line.startsWith(marker));
      expect(resultLine, execution.stderr).toBeDefined();
      const decoded: unknown = JSON.parse(resultLine?.slice(marker.length) ?? "null");
      expect(decoded).toMatchObject({
        protocolVersion: 1,
        timeoutCode: "PYTHON_ENGINE_TIMEOUT",
        capturedStderr: true,
        cleanExit: true,
        pathWithSpaces: true,
        unicodeRoundTrip: true,
        packagedEngine: Boolean(process.env["CODENTUM_PACKAGED_ENGINE_PATH"]),
      });
      if (process.env["CODENTUM_PACKAGED_ENGINE_PATH"] !== undefined) {
        expect(decoded).toMatchObject({ pythonPathIsolated: true });
      }
      console.info(`[electron-python-probe] ${JSON.stringify(decoded)}`);
    },
    45_000,
  );
});
