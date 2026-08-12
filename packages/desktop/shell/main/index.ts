import { Buffer } from 'node:buffer'
import { writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import {
  app,
  BrowserWindow,
  dialog,
  ipcMain,
  Menu,
  nativeImage,
  shell,
  Tray,
  type IpcMainInvokeEvent,
  type WebContents
} from 'electron'
import { StateHub } from '../../data'
import { IPC_CHANNELS } from '../../shared/ipc'
import { MAX_DRAFT_ATTACHMENTS, type EngineHandshake, type OperatorAction, type OperatorCommand, type ProjectSelectionKind } from '../../shared/protocol'
import { SidecarManager } from './python-engine/SidecarManager'
import { RequirementDraftStore } from './requirement-draft-store'

const ALLOWED_ACTIONS = new Set<OperatorAction>([
  'submit_requirement',
  'confirm_plan',
  'pause_at_safe_point',
  'resume',
  'stop',
  'stop_keep_memory',
  'fork_from_checkpoint',
  'append_prompt',
  'insert_module'
])

let mainWindow: BrowserWindow | undefined
let tray: Tray | undefined
let stateHub: StateHub | undefined
let sidecar: SidecarManager | undefined
let draftStore: RequirementDraftStore | undefined
let shutdownStarted = false
let shutdownComplete = false
const watchers = new Map<number, () => void>()

function assertTrustedSender(event: IpcMainInvokeEvent): void {
  const frame = event.senderFrame
  if (
    frame === null ||
    mainWindow === undefined ||
    event.sender !== mainWindow.webContents ||
    frame !== event.sender.mainFrame ||
    frame.url !== event.sender.getURL()
  ) throw new Error('Rejected IPC from an untrusted renderer')
}

function assertSourceId(value: unknown): asserts value is string {
  if (typeof value !== 'string' || value.length < 1 || value.length > 256) {
    throw new TypeError('A valid state source id is required')
  }
}

function assertProjectSelectionKind(value: unknown): asserts value is ProjectSelectionKind {
  if (value !== 'file' && value !== 'folder') throw new TypeError('A valid project selection kind is required')
}

function assertCommand(value: unknown): asserts value is OperatorCommand {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw new TypeError('Invalid command envelope')
  const command = value as Record<string, unknown>
  if (
    typeof command['commandId'] !== 'string' || command['commandId'].length < 8 || command['commandId'].length > 160 ||
    typeof command['runId'] !== 'string' || command['runId'].length < 1 || command['runId'].length > 256 ||
    typeof command['expectedRevision'] !== 'number' || !Number.isSafeInteger(command['expectedRevision']) || command['expectedRevision'] < 0 ||
    typeof command['action'] !== 'string' || !ALLOWED_ACTIONS.has(command['action'] as OperatorAction) ||
    typeof command['requestedAt'] !== 'string' || Number.isNaN(Date.parse(command['requestedAt'])) ||
    typeof command['target'] !== 'object' || command['target'] === null || Array.isArray(command['target']) ||
    typeof command['payload'] !== 'object' || command['payload'] === null || Array.isArray(command['payload'])
  ) throw new TypeError('Invalid command envelope')
  const target = command['target'] as Record<string, unknown>
  if (typeof target['agentId'] !== 'string' || target['agentId'].length < 1 || target['agentId'].length > 160) {
    throw new TypeError('Invalid command target')
  }
}

function fixtureRoot(): string {
  return app.isPackaged
    ? resolve(process.resourcesPath, 'fixtures', 'golden-state')
    : resolve(app.getAppPath(), '..', '..', 'fixtures', 'golden-state')
}

async function chooseProject(kind: ProjectSelectionKind): Promise<Awaited<ReturnType<StateHub['selectProject']>> | null> {
  if (mainWindow === undefined || stateHub === undefined) return null
  const selectingFile = kind === 'file'
  const result = await dialog.showOpenDialog(mainWindow, {
    title: selectingFile ? '选择任意文件' : '选择项目文件夹',
    buttonLabel: selectingFile ? '打开文件' : '打开文件夹',
    properties: selectingFile ? ['openFile', 'dontAddToRecent'] : ['openDirectory', 'dontAddToRecent']
  })
  const selected = result.filePaths[0]
  if (result.canceled || selected === undefined) return null
  const descriptor = await stateHub.selectProject(selectingFile ? dirname(selected) : selected)
  if (descriptor.rootPath === undefined) throw new Error('Selected project did not provide a canonical root path')
  await sidecar?.bindProject(descriptor.rootPath)
  return descriptor
}

async function registerEngineProjectSource(handshake: EngineHandshake): Promise<void> {
  if (stateHub === undefined || !handshake.connected || handshake.projectRoot === undefined) return
  try {
    await stateHub.selectProject(handshake.projectRoot)
  } catch {
    // The user can still choose a project manually; do not block app startup here.
  }
}

async function chooseDraftFiles(scopeId: string): Promise<Awaited<ReturnType<RequirementDraftStore['load']>>> {
  if (draftStore === undefined) throw new Error('Requirement draft store is unavailable')
  if (mainWindow === undefined) return draftStore.load(scopeId)
  const result = await dialog.showOpenDialog(mainWindow, {
    title: '添加文件到需求草稿',
    buttonLabel: '添加文件',
    properties: ['openFile', 'multiSelections', 'dontAddToRecent'],
    message: `可从电脑任意位置选择任意文件类型；一项需求最多添加 ${MAX_DRAFT_ATTACHMENTS} 个文件或文件夹`
  })
  if (result.canceled) return draftStore.load(scopeId)
  return draftStore.addFiles(scopeId, result.filePaths)
}

async function chooseDraftFolders(scopeId: string): Promise<Awaited<ReturnType<RequirementDraftStore['load']>>> {
  if (draftStore === undefined) throw new Error('Requirement draft store is unavailable')
  if (mainWindow === undefined) return draftStore.load(scopeId)
  const result = await dialog.showOpenDialog(mainWindow, {
    title: '添加文件夹到需求草稿',
    buttonLabel: '添加文件夹',
    properties: ['openDirectory', 'multiSelections', 'dontAddToRecent'],
    message: `可从电脑任意位置选择文件夹；一项需求最多添加 ${MAX_DRAFT_ATTACHMENTS} 个文件或文件夹`
  })
  if (result.canceled) return draftStore.load(scopeId)
  return draftStore.addFolders(scopeId, result.filePaths)
}

async function exportTaskRecord(suggestedName: unknown, markdown: unknown): Promise<boolean> {
  if (mainWindow === undefined) return false
  if (typeof suggestedName !== 'string' || suggestedName.length < 1 || suggestedName.length > 160) throw new TypeError('Invalid export filename')
  if (typeof markdown !== 'string' || Buffer.byteLength(markdown, 'utf8') > 5 * 1024 * 1024) throw new TypeError('Invalid chat export')
  const safeName = suggestedName.replace(/[<>:"/\\|?*\u0000-\u001f]/gu, '_').replace(/[. ]+$/u, '').slice(0, 120) || 'Codentum-chat'
  const result = await dialog.showSaveDialog(mainWindow, {
    title: '导出任务记录',
    defaultPath: `${safeName}.md`,
    buttonLabel: '导出',
    filters: [{ name: 'Markdown', extensions: ['md'] }]
  })
  if (result.canceled || result.filePath === '') return false
  await writeFile(result.filePath, markdown, { encoding: 'utf8', flag: 'w', mode: 0o600 })
  return true
}

function cleanupWatcher(contents: WebContents): void {
  watchers.get(contents.id)?.()
  watchers.delete(contents.id)
}

function registerIpc(): void {
  ipcMain.handle(IPC_CHANNELS.listSources, (event) => {
    assertTrustedSender(event)
    return stateHub?.listSources() ?? []
  })
  ipcMain.handle(IPC_CHANNELS.readSnapshot, async (event, sourceId: unknown) => {
    assertTrustedSender(event)
    assertSourceId(sourceId)
    if (stateHub === undefined) throw new Error('State source is unavailable')
    return stateHub.read(sourceId)
  })
  ipcMain.handle(IPC_CHANNELS.selectProject, async (event, kind: unknown) => {
    assertTrustedSender(event)
    assertProjectSelectionKind(kind)
    return chooseProject(kind)
  })
  ipcMain.handle(IPC_CHANNELS.selectDraftFiles, async (event, scopeId: unknown) => {
    assertTrustedSender(event)
    assertSourceId(scopeId)
    return chooseDraftFiles(scopeId)
  })
  ipcMain.handle(IPC_CHANNELS.selectDraftFolders, async (event, scopeId: unknown) => {
    assertTrustedSender(event)
    assertSourceId(scopeId)
    return chooseDraftFolders(scopeId)
  })
  ipcMain.handle(IPC_CHANNELS.loadRequirementDraft, async (event, scopeId: unknown) => {
    assertTrustedSender(event)
    assertSourceId(scopeId)
    if (draftStore === undefined) throw new Error('Requirement draft store is unavailable')
    return draftStore.load(scopeId)
  })
  ipcMain.handle(IPC_CHANNELS.saveRequirementDraft, async (event, scopeId: unknown, draft: unknown) => {
    assertTrustedSender(event)
    assertSourceId(scopeId)
    if (draftStore === undefined) throw new Error('Requirement draft store is unavailable')
    await draftStore.save(scopeId, draft)
  })
  ipcMain.handle(IPC_CHANNELS.moveRequirementDraft, async (event, sourceScopeId: unknown, targetScopeId: unknown) => {
    assertTrustedSender(event)
    assertSourceId(sourceScopeId)
    assertSourceId(targetScopeId)
    if (draftStore === undefined) throw new Error('Requirement draft store is unavailable')
    return draftStore.move(sourceScopeId, targetScopeId)
  })
  ipcMain.handle(IPC_CHANNELS.discardDraftAttachment, async (event, scopeId: unknown, attachmentId: unknown) => {
    assertTrustedSender(event)
    assertSourceId(scopeId)
    if (typeof attachmentId !== 'string') throw new TypeError('A valid attachment id is required')
    if (draftStore === undefined) throw new Error('Requirement draft store is unavailable')
    return draftStore.discard(scopeId, attachmentId)
  })
  ipcMain.handle(IPC_CHANNELS.exportTaskRecord, async (event, suggestedName: unknown, markdown: unknown) => {
    assertTrustedSender(event)
    return exportTaskRecord(suggestedName, markdown)
  })
  ipcMain.handle(IPC_CHANNELS.watchSource, async (event, sourceId: unknown) => {
    assertTrustedSender(event)
    assertSourceId(sourceId)
    if (stateHub === undefined) throw new Error('State source is unavailable')
    cleanupWatcher(event.sender)
    const unsubscribe = stateHub.watch(sourceId, (snapshot) => {
      if (!event.sender.isDestroyed()) event.sender.send(IPC_CHANNELS.snapshotChanged, snapshot)
    })
    watchers.set(event.sender.id, unsubscribe)
    event.sender.once('destroyed', () => cleanupWatcher(event.sender))
  })
  ipcMain.handle(IPC_CHANNELS.engineHandshake, async (event) => {
    assertTrustedSender(event)
    if (sidecar === undefined) throw new Error('Sidecar manager is unavailable')
    return sidecar.handshake()
  })
  ipcMain.handle(IPC_CHANNELS.engineCommand, async (event, command: unknown) => {
    assertTrustedSender(event)
    assertCommand(command)
    if (sidecar === undefined) throw new Error('Sidecar manager is unavailable')
    let prepared = command
    if (command.action === 'submit_requirement') {
      if (draftStore === undefined || stateHub === undefined) throw new Error('Requirement draft store is unavailable')
      const draftScope = command.payload['draftScope']
      if (typeof draftScope !== 'string') throw new TypeError('Requirement command is missing its draft scope')
      prepared = await draftStore.prepareRequirementCommand(command, stateHub.projectRoot(draftScope))
    }
    return sidecar.command(prepared)
  })
}

function createApplicationMenu(): void {
  const template: Electron.MenuItemConstructorOptions[] = [
    {
      label: '文件',
      submenu: [
        { label: '打开项目文件夹…', accelerator: 'CmdOrCtrl+O', click: () => void chooseProject('folder') },
        { type: 'separator' },
        { role: 'quit', label: '退出 Codentum' }
      ]
    },
    {
      label: '窗口',
      submenu: [
        { role: 'reload', label: '重新加载' },
        { role: 'togglefullscreen', label: '全屏' },
        { role: 'minimize', label: '最小化' }
      ]
    }
  ]
  Menu.setApplicationMenu(Menu.buildFromTemplate(template))
}

function createTray(): void {
  const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"><rect width="32" height="32" rx="10" fill="#12b886"/><path d="M9 11h14v4H13v6h10v4H9z" fill="white"/></svg>'
  const icon = nativeImage.createFromDataURL(`data:image/svg+xml;base64,${Buffer.from(svg).toString('base64')}`)
  if (icon.isEmpty()) return
  tray = new Tray(icon.resize({ width: 16, height: 16 }))
  tray.setToolTip('Codentum')
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: '显示 Codentum', click: () => { mainWindow?.show(); mainWindow?.focus() } },
    { role: 'quit', label: '退出' }
  ]))
  tray.on('double-click', () => { mainWindow?.show(); mainWindow?.focus() })
}

function createWindow(): BrowserWindow {
  const window = new BrowserWindow({
    width: 1460,
    height: 920,
    minWidth: 1120,
    minHeight: 720,
    show: false,
    backgroundColor: '#f7f8fa',
    title: 'Codentum',
    webPreferences: {
      preload: resolve(__dirname, '../preload/index.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true
    }
  })
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https:\/\//u.test(url)) void shell.openExternal(url)
    return { action: 'deny' }
  })
  window.webContents.on('will-navigate', (event, url) => {
    const current = window.webContents.getURL()
    if (url !== current) event.preventDefault()
  })
  window.once('ready-to-show', () => window.show())
  const rendererUrl = app.isPackaged ? undefined : process.env['ELECTRON_RENDERER_URL']
  if (rendererUrl !== undefined) {
    const parsed = new URL(rendererUrl)
    if (!['localhost', '127.0.0.1'].includes(parsed.hostname) || !['http:', 'https:'].includes(parsed.protocol)) {
      throw new Error('Development renderer URL must use localhost or 127.0.0.1')
    }
    void window.loadURL(parsed.href)
  } else {
    void window.loadFile(resolve(__dirname, '../renderer/index.html'))
  }
  return window
}

void app.whenReady().then(async () => {
  stateHub = new StateHub({
    fixtureRoot: process.env['CODENTUM_ENABLE_FIXTURES'] === '1' ? fixtureRoot() : null,
    pollIntervalMs: 1_000,
    staleAfterMs: 30_000
  })
  draftStore = new RequirementDraftStore(resolve(app.getPath('userData'), 'requirement-drafts'))
  await draftStore.initialize()
  sidecar = new SidecarManager(app)
  registerIpc()
  createApplicationMenu()
  createTray()
  const startupHandshake = await sidecar.start()
  await registerEngineProjectSource(startupHandshake)
  mainWindow = createWindow()
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) mainWindow = createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

app.on('before-quit', (event) => {
  if (shutdownComplete) return
  event.preventDefault()
  if (shutdownStarted) return
  shutdownStarted = true
  for (const unsubscribe of watchers.values()) unsubscribe()
  watchers.clear()
  stateHub?.close()
  void Promise.resolve(sidecar?.close()).catch(() => undefined).finally(() => {
    shutdownComplete = true
    app.quit()
  })
})
