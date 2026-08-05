"use strict";

const { mkdirSync, mkdtempSync } = require("node:fs");
const { access, mkdir, writeFile } = require("node:fs/promises");
const { tmpdir } = require("node:os");
const { basename, dirname, join, resolve } = require("node:path");
const { spawnSync } = require("node:child_process");
const { pathToFileURL } = require("node:url");
const electron = require("electron");

const isElectronRuntime = typeof electron !== "string";
const app = isElectronRuntime ? electron.app : undefined;
const BrowserWindow = isElectronRuntime ? electron.BrowserWindow : undefined;

const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
const STARTUP_TIMEOUT_MS = 20_000;
const MIN_SCREENSHOT_BYTES = 1_024;
const MIN_SCREENSHOT_WIDTH = 600;
const MIN_SCREENSHOT_HEIGHT = 400;

const SNAPSHOT_SCENARIOS = [
  { id: "empty", label: "空项目" },
  { id: "mid-flight", label: "开发进行中" },
  { id: "blocked", label: "阻塞与待审批" },
];

function fail(message) {
  throw new Error(`[screenshot-smoke] ${message}`);
}

function withTimeout(promise, timeoutMs, label) {
  let timeout;
  const deadline = new Promise((_, reject) => {
    timeout = setTimeout(() => {
      reject(new Error(`[screenshot-smoke] ${label} timed out after ${timeoutMs} ms`));
    }, timeoutMs);
  });

  return Promise.race([promise, deadline]).finally(() => {
    clearTimeout(timeout);
  });
}

async function waitForMainWindow() {
  const existingWindow = BrowserWindow.getAllWindows()[0];
  if (existingWindow !== undefined) {
    return existingWindow;
  }

  return withTimeout(
    new Promise((resolveWindow) => {
      app.on("browser-window-created", (_event, window) => {
        resolveWindow(window);
      });
    }),
    STARTUP_TIMEOUT_MS,
    "waiting for the application window",
  );
}

async function waitForRenderer(window) {
  if (window.webContents.isLoadingMainFrame()) {
    await withTimeout(
      new Promise((resolveLoad, rejectLoad) => {
        const onFinished = () => {
          cleanup();
          resolveLoad();
        };
        const onFailed = (_event, errorCode, errorDescription, validatedURL, isMainFrame) => {
          if (!isMainFrame) return;
          cleanup();
          rejectLoad(
            new Error(
              `[screenshot-smoke] renderer failed to load (${errorCode} ${errorDescription}): ${validatedURL}`,
            ),
          );
        };
        const cleanup = () => {
          window.webContents.removeListener("did-finish-load", onFinished);
          window.webContents.removeListener("did-fail-load", onFailed);
        };

        window.webContents.once("did-finish-load", onFinished);
        window.webContents.on("did-fail-load", onFailed);
      }),
      STARTUP_TIMEOUT_MS,
      "loading the renderer",
    );
  }

  const rendererState = await withTimeout(
    window.webContents.executeJavaScript(`
      (async () => {
        if (document.fonts !== undefined) await document.fonts.ready;
        await new Promise((resolveFrame) => {
          requestAnimationFrame(() => requestAnimationFrame(resolveFrame));
        });
        return {
          readyState: document.readyState,
          rootChildCount: document.getElementById("root")?.childElementCount ?? 0,
          bodyTextLength: document.body.innerText.trim().length,
        };
      })()
    `),
    STARTUP_TIMEOUT_MS,
    "waiting for the first rendered frame",
  );

  if (rendererState.readyState !== "complete") {
    fail(`renderer readyState is ${rendererState.readyState}, expected complete`);
  }
  if (rendererState.rootChildCount < 1) {
    fail("React did not mount any content into #root");
  }
  if (rendererState.bodyTextLength < 20) {
    fail(`rendered body is unexpectedly empty (${rendererState.bodyTextLength} characters)`);
  }
}

async function waitForUi(window, expression, label) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < STARTUP_TIMEOUT_MS) {
    const ready = await window.webContents.executeJavaScript(`Boolean(${expression})`);
    if (ready) return;
    await new Promise((resolveDelay) => setTimeout(resolveDelay, 80));
  }
  fail(`${label} timed out after ${STARTUP_TIMEOUT_MS} ms`);
}

async function settleUi(window) {
  await window.webContents.executeJavaScript(`
    new Promise((resolveFrame) => {
      requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(resolveFrame, 80)));
    })
  `);
}

async function clickButton(window, selector, text) {
  const clicked = await window.webContents.executeJavaScript(`
    (() => {
      const button = Array.from(document.querySelectorAll(${JSON.stringify(selector)}))
        .find((candidate) => candidate.textContent?.includes(${JSON.stringify(text)}));
      if (!(button instanceof HTMLButtonElement)) return false;
      button.click();
      return true;
    })()
  `);
  if (!clicked) fail(`could not find button "${text}" in ${selector}`);
  await settleUi(window);
}

async function clickFirst(window, selector, label) {
  const clicked = await window.webContents.executeJavaScript(`
    (() => {
      const target = document.querySelector(${JSON.stringify(selector)});
      if (!(target instanceof HTMLButtonElement)) return false;
      target.click();
      return true;
    })()
  `);
  if (!clicked) fail(`could not click ${label} (${selector})`);
  await settleUi(window);
}

async function selectSnapshot(window, scenario) {
  const selected = await window.webContents.executeJavaScript(`
    (() => {
      const select = document.querySelector(".product-source-picker select");
      if (!(select instanceof HTMLSelectElement)) return false;
      select.value = ${JSON.stringify(scenario.id)};
      select.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    })()
  `);
  if (!selected) fail(`could not select snapshot ${scenario.id}`);
  await waitForUi(
    window,
    `document.querySelector(".operations-live-chip")?.textContent?.includes(${JSON.stringify(scenario.label)})`,
    `waiting for snapshot ${scenario.id}`,
  );
  await settleUi(window);
}

function assertPng(png, nativeSize) {
  if (png.length < MIN_SCREENSHOT_BYTES) {
    fail(`PNG is unexpectedly small (${png.length} bytes)`);
  }
  if (!png.subarray(0, PNG_SIGNATURE.length).equals(PNG_SIGNATURE)) {
    fail("captured file does not have a valid PNG signature");
  }

  // The first PNG chunk is IHDR. Its width and height are big-endian uint32 values.
  const width = png.readUInt32BE(16);
  const height = png.readUInt32BE(20);
  if (width < MIN_SCREENSHOT_WIDTH || height < MIN_SCREENSHOT_HEIGHT) {
    fail(`PNG dimensions are unexpectedly small (${width}x${height})`);
  }
  if (width !== nativeSize.width || height !== nativeSize.height) {
    fail(
      `PNG dimensions ${width}x${height} do not match Electron image dimensions `
        + `${nativeSize.width}x${nativeSize.height}`,
    );
  }

  return { width, height };
}

function createArtifactDirectory() {
  const configuredDirectory = process.env["CODENTUM_SCREENSHOT_DIR"];
  if (configuredDirectory !== undefined && configuredDirectory.trim().length > 0) {
    const artifactDirectory = resolve(configuredDirectory);
    mkdirSync(artifactDirectory, { recursive: true });
    return artifactDirectory;
  }

  return mkdtempSync(join(tmpdir(), "codentum-desktop-smoke-"));
}

async function captureNamed(window, artifactDirectory, fileName) {
  const screenshotPath = join(artifactDirectory, fileName);
  const capturedImage = await window.webContents.capturePage();
  const png = capturedImage.toPNG();
  const dimensions = assertPng(png, capturedImage.getSize());
  await mkdir(dirname(screenshotPath), { recursive: true });
  await writeFile(screenshotPath, png);
  return {
    path: screenshotPath,
    file: basename(screenshotPath),
    width: dimensions.width,
    height: dimensions.height,
    bytes: png.length,
  };
}

async function run() {
  // These settings must be applied synchronously, before Electron emits ready.
  app.disableHardwareAcceleration();

  const desktopRoot = resolve(__dirname, "..");
  const mainEntry = join(desktopRoot, "out", "main", "index.js");
  const artifactDirectory = createArtifactDirectory();

  if (process.env["CODENTUM_GOLDEN_STATE_ROOT"] === undefined) {
    process.env["CODENTUM_GOLDEN_STATE_ROOT"] = resolve(
      desktopRoot,
      "..",
      "..",
      "fixtures",
      "golden-state",
    );
  }

  // Keep Chromium caches and Electron preferences out of the developer's normal profile.
  app.setPath("userData", join(artifactDirectory, "electron-user-data"));

  try {
    await access(mainEntry);
  } catch {
    fail(`missing ${mainEntry}; run \"npm run build\" first`);
  }

  await import(pathToFileURL(mainEntry).href);
  await app.whenReady();

  const mainWindow = await waitForMainWindow();
  await waitForRenderer(mainWindow);

  const captures = [];
  captures.push(await captureNamed(mainWindow, artifactDirectory, "industry-home.png"));

  await clickButton(mainWindow, ".product-app-nav button", "Agent 状态");
  await waitForUi(mainWindow, `document.querySelector(".operations-page") !== null`, "opening Agent operations");

  for (const scenario of SNAPSHOT_SCENARIOS) {
    await selectSnapshot(mainWindow, scenario);
    captures.push(await captureNamed(mainWindow, artifactDirectory, `${scenario.id}-agents.png`));
  }

  await selectSnapshot(mainWindow, SNAPSHOT_SCENARIOS[1]);
  for (const section of ["甘特图", "依赖图", "成本仪表"]) {
    await clickButton(mainWindow, ".operations-tabs button", section);
    const fileStem = section === "甘特图" ? "gantt" : section === "依赖图" ? "dependencies" : "cost";
    captures.push(await captureNamed(mainWindow, artifactDirectory, `mid-flight-${fileStem}.png`));
  }

  // Exercise the dashboard's drill-down controls, not only its navigation tabs.
  await clickButton(mainWindow, ".operations-tabs button", "Agent 状态");
  await clickFirst(mainWindow, ".agent-status-row", "an Agent row");
  await waitForUi(mainWindow, `document.querySelector(".agent-detail-panel") !== null`, "opening Agent details");

  await clickButton(mainWindow, ".operations-tabs button", "甘特图");
  await clickFirst(mainWindow, ".gantt-bar", "a Gantt task bar");
  await waitForUi(
    mainWindow,
    `document.querySelector(".agent-detail-panel") !== null && document.querySelector(".operations-tabs .is-active")?.textContent?.includes("Agent 状态")`,
    "drilling down from Gantt to Agent details",
  );

  await clickButton(mainWindow, ".operations-tabs button", "依赖图");
  await clickFirst(mainWindow, ".dependency-node", "a dependency node");
  await waitForUi(mainWindow, `document.querySelector(".dependency-detail") !== null`, "opening dependency details");

  await clickButton(mainWindow, ".operations-tabs button", "成本仪表");
  await clickButton(mainWindow, ".cost-dimension-switch button", "按模型");
  await waitForUi(
    mainWindow,
    `document.querySelector(".cost-dimension-switch .is-active")?.textContent?.includes("按模型")`,
    "switching the cost dimension",
  );
  await clickFirst(mainWindow, ".packet-cost-list button", "a packet cost row");
  await waitForUi(mainWindow, `document.querySelector(".agent-detail-panel") !== null`, "drilling down from cost to Agent details");

  process.stdout.write(
    [
      "[screenshot-smoke] PASS",
      `artifact directory: ${artifactDirectory}`,
      `captures: ${captures.length}`,
      ...captures.map((capture) => (
        `${capture.file}: ${capture.width}x${capture.height}, ${capture.bytes} bytes`
      )),
    ].join("\n") + "\n",
  );
}

function launchElectronRuntime(electronExecutable) {
  const environment = { ...process.env };
  delete environment["ELECTRON_RUN_AS_NODE"];

  const result = spawnSync(electronExecutable, [__filename], {
    env: environment,
    stdio: "inherit",
    windowsHide: true,
  });

  if (result.error !== undefined) {
    process.stderr.write(`${result.error.stack ?? result.error.message}\n`);
    process.exitCode = 1;
    return;
  }
  process.exitCode = result.status ?? 1;
}

if (!isElectronRuntime) {
  launchElectronRuntime(electron);
} else {
  run()
    .then(() => {
      for (const window of BrowserWindow.getAllWindows()) window.destroy();
      app.exit(0);
    })
    .catch((error) => {
      process.stderr.write(`${error instanceof Error ? error.stack : String(error)}\n`);
      for (const window of BrowserWindow.getAllWindows()) window.destroy();
      app.exit(1);
    });
}
