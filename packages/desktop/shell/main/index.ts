import { app, BrowserWindow, shell } from "electron";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { GoldenStateSource } from "./state/GoldenStateSource";
import { registerStateSourceIpc } from "./state/state-ipc";

const currentDirectory = dirname(fileURLToPath(import.meta.url));

function createMainWindow(): BrowserWindow {
  const mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 640,
    minHeight: 480,
    show: false,
    backgroundColor: "#f4f6f3",
    title: "Codentum",
    webPreferences: {
      preload: join(currentDirectory, "../preload/index.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  mainWindow.once("ready-to-show", () => {
    mainWindow.show();
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("https://")) {
      void shell.openExternal(url);
    }
    return { action: "deny" };
  });

  mainWindow.webContents.on("preload-error", (_event, preloadPath, error) => {
    console.error(`[preload-error] ${preloadPath}: ${error.message}`);
  });
  mainWindow.webContents.on("did-fail-load", (_event, code, description, url, isMainFrame) => {
    if (isMainFrame) {
      console.error(`[did-fail-load] ${code} ${description}: ${url}`);
    }
  });

  const developmentServerUrl = process.env["ELECTRON_RENDERER_URL"];
  if (developmentServerUrl !== undefined) {
    void mainWindow.loadURL(developmentServerUrl);
  } else {
    void mainWindow.loadFile(join(currentDirectory, "../renderer/index.html"));
  }

  return mainWindow;
}

app.whenReady().then(() => {
  const configuredGoldenStateRoot = process.env["CODENTUM_GOLDEN_STATE_ROOT"];
  const goldenStateRoot = configuredGoldenStateRoot
    ?? resolve(app.getAppPath(), "../../fixtures/golden-state");
  registerStateSourceIpc(new GoldenStateSource(goldenStateRoot));

  createMainWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createMainWindow();
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
