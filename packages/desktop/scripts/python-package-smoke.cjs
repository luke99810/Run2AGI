"use strict";

const { existsSync, mkdtempSync, rmSync } = require("node:fs");
const { tmpdir } = require("node:os");
const { join, resolve } = require("node:path");
const { spawnSync } = require("node:child_process");

const PYINSTALLER_VERSION = "6.15.0";
const STEP_TIMEOUT_MS = 240_000;

function runChecked(label, executable, args, environment = process.env) {
  process.stdout.write(`[python-package-smoke] ${label}\n`);
  const result = spawnSync(executable, args, {
    env: environment,
    stdio: "inherit",
    timeout: STEP_TIMEOUT_MS,
    windowsHide: true,
  });
  if (result.error !== undefined) throw result.error;
  if (result.status !== 0) {
    throw new Error(`${label} failed with status ${String(result.status)} and signal ${String(result.signal)}`);
  }
}

function main() {
  const desktopRoot = resolve(__dirname, "..");
  const repositoryRoot = resolve(desktopRoot, "..", "..");
  const probeSource = join(
    repositoryRoot,
    "packages",
    "delivery",
    "codentum_delivery",
    "packaging",
    "python_engine_probe.py",
  );
  const temporaryRoot = mkdtempSync(join(tmpdir(), "codentum pyinstaller package "));
  const virtualEnvironment = join(temporaryRoot, "isolated build environment");
  const virtualPython = process.platform === "win32"
    ? join(virtualEnvironment, "Scripts", "python.exe")
    : join(virtualEnvironment, "bin", "python");
  const distDirectory = join(temporaryRoot, "dist");
  const workDirectory = join(temporaryRoot, "work");
  const specDirectory = join(temporaryRoot, "spec");
  const packagedEngine = join(
    distDirectory,
    `codentum-python-engine${process.platform === "win32" ? ".exe" : ""}`,
  );
  const basePython = process.env["CODENTUM_PYTHON_EXECUTABLE"] || "python";
  const keepArtifacts = process.env["CODENTUM_KEEP_PACKAGE_PROBE"] === "1";

  try {
    runChecked("creating an isolated Python build environment", basePython, [
      "-m", "venv", virtualEnvironment,
    ]);
    runChecked(`installing PyInstaller ${PYINSTALLER_VERSION}`, virtualPython, [
      "-m", "pip", "install", "--disable-pip-version-check", `PyInstaller==${PYINSTALLER_VERSION}`,
    ]);
    runChecked("building the one-file Python sidecar", virtualPython, [
      "-m", "PyInstaller",
      "--noconfirm",
      "--clean",
      "--onefile",
      "--log-level", "WARN",
      "--name", "codentum-python-engine",
      "--distpath", distDirectory,
      "--workpath", workDirectory,
      "--specpath", specDirectory,
      probeSource,
    ]);
    if (!existsSync(packagedEngine)) {
      throw new Error(`PyInstaller did not create ${packagedEngine}`);
    }

    const vitestEntry = join(desktopRoot, "node_modules", "vitest", "vitest.mjs");
    runChecked(
      "launching the packaged sidecar from a real Electron main process",
      process.execPath,
      [
        vitestEntry,
        "run",
        "shell/main/python-engine/PythonEngineClient.test.ts",
        "--reporter=verbose",
      ],
      { ...process.env, CODENTUM_PACKAGED_ENGINE_PATH: packagedEngine },
    );
    process.stdout.write("[python-package-smoke] PASS: packaged sidecar launched successfully\n");
  } finally {
    if (keepArtifacts) {
      process.stdout.write(`[python-package-smoke] retained artifacts: ${temporaryRoot}\n`);
    } else {
      rmSync(temporaryRoot, { recursive: true, force: true, maxRetries: 3, retryDelay: 200 });
    }
  }
}

try {
  main();
} catch (error) {
  process.stderr.write(`${error instanceof Error ? error.stack : String(error)}\n`);
  process.exitCode = 1;
}
