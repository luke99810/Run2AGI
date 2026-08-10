import { spawn } from 'node:child_process'
import { createServer } from 'node:net'
import { mkdir, mkdtemp, rm, stat, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const scriptDirectory = dirname(fileURLToPath(import.meta.url))
const desktopRoot = resolve(scriptDirectory, '..')
const outputDirectory = resolve(desktopRoot, 'artifacts', 'screenshots')
const mainEntry = resolve(desktopRoot, 'out', 'main', 'index.js')
const rendererEntry = resolve(desktopRoot, 'out', 'renderer', 'index.html')
const electronExecutable = resolve(
  desktopRoot,
  'node_modules',
  'electron',
  'dist',
  process.platform === 'win32' ? 'electron.exe' : 'electron'
)

const FIXTURE_IDS = [
  'fixture:blocked',
  'fixture:empty',
  'fixture:mid-flight'
]

const SCREENSHOTS = [
  { name: '01-home.png', navigation: '新对话', heading: '要做什么软件？' },
  { name: '02-execution.png', navigation: '执行中心', heading: '执行中心' },
  { name: '03-validation.png', navigation: '集成与验证', heading: '集成与验证' },
  { name: '04-dependency.png', navigation: '依赖关系', heading: '依赖关系' },
  { name: '05-cost.png', navigation: '成本', heading: '成本' },
  { name: '06-team.png', navigation: '研发团队', heading: '研发团队' }
]

const delay = (milliseconds) => new Promise((resolveDelay) => setTimeout(resolveDelay, milliseconds))

function formatError(error) {
  return error instanceof Error ? (error.stack ?? error.message) : String(error)
}

async function assertBuildExists() {
  const required = [mainEntry, rendererEntry, electronExecutable]
  for (const path of required) {
    try {
      await stat(path)
    } catch {
      throw new Error(`Missing build prerequisite: ${path}\nRun \"npm run build\" before the screenshot smoke test.`)
    }
  }
}

async function reservePort() {
  return new Promise((resolvePort, rejectPort) => {
    const server = createServer()
    server.unref()
    server.once('error', rejectPort)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      if (address === null || typeof address === 'string') {
        server.close()
        rejectPort(new Error('Could not reserve a local DevTools port.'))
        return
      }
      server.close((error) => error === undefined ? resolvePort(address.port) : rejectPort(error))
    })
  })
}

async function waitForDevToolsTarget(port, child, timeoutMs = 20_000) {
  const deadline = Date.now() + timeoutMs
  let lastFailure = 'target was not published'
  while (Date.now() < deadline) {
    if (child.exitCode !== null) {
      throw new Error(`Electron exited before its renderer became available (exit ${child.exitCode}).`)
    }
    try {
      const response = await fetch(`http://127.0.0.1:${port}/json/list`, {
        signal: AbortSignal.timeout(1_000)
      })
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const targets = await response.json()
      const target = targets.find((candidate) =>
        candidate.type === 'page' &&
        typeof candidate.webSocketDebuggerUrl === 'string' &&
        (candidate.url.startsWith('file://') || candidate.title === 'Codentum')
      )
      if (target !== undefined) return target
      lastFailure = `published targets: ${targets.map((candidate) => `${candidate.type}:${candidate.url}`).join(', ')}`
    } catch (error) {
      lastFailure = formatError(error)
    }
    await delay(100)
  }
  throw new Error(`Timed out waiting for the Electron renderer: ${lastFailure}`)
}

class DevToolsClient {
  constructor(url) {
    this.url = url
    this.nextId = 0
    this.pending = new Map()
    this.listeners = new Set()
    this.socket = undefined
  }

  async connect(timeoutMs = 10_000) {
    const socket = new WebSocket(this.url)
    this.socket = socket
    await new Promise((resolveOpen, rejectOpen) => {
      const timer = setTimeout(() => rejectOpen(new Error('Timed out opening the DevTools WebSocket.')), timeoutMs)
      socket.addEventListener('open', () => {
        clearTimeout(timer)
        resolveOpen()
      }, { once: true })
      socket.addEventListener('error', () => {
        clearTimeout(timer)
        rejectOpen(new Error('The DevTools WebSocket failed to open.'))
      }, { once: true })
    })
    socket.addEventListener('message', (event) => this.handleMessage(event.data))
    socket.addEventListener('close', () => {
      for (const pending of this.pending.values()) pending.reject(new Error('The DevTools connection closed.'))
      this.pending.clear()
    })
  }

  onEvent(listener) {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  handleMessage(raw) {
    let message
    try {
      message = JSON.parse(typeof raw === 'string' ? raw : Buffer.from(raw).toString('utf8'))
    } catch {
      return
    }
    if (typeof message.id === 'number') {
      const pending = this.pending.get(message.id)
      if (pending === undefined) return
      this.pending.delete(message.id)
      if (message.error !== undefined) pending.reject(new Error(`${pending.method}: ${message.error.message}`))
      else pending.resolve(message.result ?? {})
      return
    }
    if (typeof message.method === 'string') {
      for (const listener of this.listeners) listener(message.method, message.params ?? {})
    }
  }

  send(method, params = {}, timeoutMs = 15_000) {
    if (this.socket === undefined || this.socket.readyState !== WebSocket.OPEN) {
      return Promise.reject(new Error(`Cannot send ${method}: DevTools is not connected.`))
    }
    const id = ++this.nextId
    return new Promise((resolveResult, rejectResult) => {
      const timer = setTimeout(() => {
        this.pending.delete(id)
        rejectResult(new Error(`Timed out waiting for ${method}.`))
      }, timeoutMs)
      this.pending.set(id, {
        method,
        resolve: (value) => {
          clearTimeout(timer)
          resolveResult(value)
        },
        reject: (error) => {
          clearTimeout(timer)
          rejectResult(error)
        }
      })
      this.socket.send(JSON.stringify({ id, method, params }))
    })
  }

  close() {
    this.socket?.close()
  }
}

async function evaluate(client, expression, label = 'renderer expression') {
  const response = await client.send('Runtime.evaluate', {
    expression,
    awaitPromise: true,
    returnByValue: true,
    userGesture: true
  })
  if (response.exceptionDetails !== undefined) {
    const exception = response.exceptionDetails.exception?.description
      ?? response.exceptionDetails.text
      ?? 'unknown renderer exception'
    throw new Error(`${label} failed: ${exception}`)
  }
  if (response.result?.type === 'undefined') return undefined
  return response.result?.value
}

async function waitFor(client, expression, description, timeoutMs = 15_000) {
  const deadline = Date.now() + timeoutMs
  let lastValue
  while (Date.now() < deadline) {
    try {
      lastValue = await evaluate(client, expression, `wait for ${description}`)
      if (lastValue) return lastValue
    } catch (error) {
      lastValue = formatError(error)
    }
    await delay(100)
  }
  const body = await evaluate(client, `document.body?.innerText?.slice(0, 1200) ?? ''`, 'read timeout diagnostics').catch(() => '')
  throw new Error(`Timed out waiting for ${description}. Last result: ${JSON.stringify(lastValue)}\nVisible text:\n${body}`)
}

async function startPageErrorMonitor(client) {
  const errors = []
  const unsubscribe = client.onEvent((method, params) => {
    if (method === 'Log.entryAdded' && params.entry?.level === 'error') {
      errors.push(`page log: ${params.entry.text ?? 'unknown error'}`)
    }
    if (method === 'Runtime.consoleAPICalled' && ['error', 'assert'].includes(params.type)) {
      const values = (params.args ?? []).map((value) => value.description ?? value.value ?? value.type)
      errors.push(`console.${params.type}: ${values.join(' ')}`)
    }
    if (method === 'Runtime.exceptionThrown') {
      const detail = params.exceptionDetails?.exception?.description
        ?? params.exceptionDetails?.text
        ?? 'unknown renderer exception'
      errors.push(`uncaught renderer exception: ${detail}`)
    }
    if (method === 'Inspector.targetCrashed') errors.push('renderer target crashed')
  })
  return {
    errors,
    stop: unsubscribe,
    async assertClean(context) {
      if (errors.length > 0) throw new Error(`${context} emitted renderer errors:\n${errors.join('\n')}`)
    }
  }
}

async function selectFixture(client, fixtureId) {
  const changed = await evaluate(client, `(() => {
    const select = document.querySelector('select[aria-label="状态来源"]')
    if (!(select instanceof HTMLSelectElement)) return false
    select.value = ${JSON.stringify(fixtureId)}
    select.dispatchEvent(new Event('change', { bubbles: true }))
    return true
  })()`, `select ${fixtureId}`)
  if (!changed) throw new Error('The visible state-source selector was not found.')
  const fixtureName = fixtureId.slice('fixture:'.length)
  await waitFor(client, `(() => {
    const select = document.querySelector('select[aria-label="状态来源"]')
    const title = document.querySelector('.home-overview h2')
    const loading = document.querySelector('.loading-line')
    return select?.value === ${JSON.stringify(fixtureId)} && title?.textContent?.trim() === ${JSON.stringify(fixtureName)} && loading === null
  })()`, `${fixtureName} snapshot to render`)
}

async function clickNavigation(client, label, expectedHeading) {
  const clicked = await evaluate(client, `(() => {
    const normalize = (value) => (value ?? '').replace(/\\s+/g, ' ').trim()
    const visible = (element) => {
      const rect = element.getBoundingClientRect()
      const style = getComputedStyle(element)
      return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden'
    }
    const button = [...document.querySelectorAll('.sidebar button')]
      .find((candidate) => visible(candidate) && normalize(candidate.querySelector(':scope > span')?.textContent) === ${JSON.stringify(label)})
    if (!(button instanceof HTMLButtonElement)) return false
    button.click()
    return true
  })()`, `click navigation ${label}`)
  if (!clicked) throw new Error(`Navigation button was not visible/clickable: ${label}`)
  await waitFor(client, `(() => {
    const normalize = (value) => (value ?? '').replace(/\\s+/g, ' ').trim()
    const heading = document.querySelector('.content-scroll h1')
    return normalize(heading?.textContent).includes(${JSON.stringify(expectedHeading)})
  })()`, `${label} view to become active`)
}

async function settleRenderer(client) {
  await evaluate(client, `new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(resolve, 80))))`, 'settle renderer')
  await evaluate(client, `(() => { const scroller = document.querySelector('.content-scroll'); if (scroller) scroller.scrollTop = 0; return true })()`, 'reset content scroll')
}

async function captureScreenshot(client, filename) {
  await settleRenderer(client)
  const { data } = await client.send('Page.captureScreenshot', {
    format: 'png',
    fromSurface: true,
    captureBeyondViewport: false
  })
  if (typeof data !== 'string' || data.length < 1_000) throw new Error(`Electron returned an empty screenshot for ${filename}.`)
  const path = resolve(outputDirectory, filename)
  await writeFile(path, Buffer.from(data, 'base64'))
  const file = await stat(path)
  if (file.size < 5_000) throw new Error(`Screenshot ${filename} is unexpectedly small (${file.size} bytes).`)
  process.stdout.write(`captured ${filename} (${file.size} bytes)\n`)
}

async function exerciseInteractiveDetails(client, navigation) {
  if (navigation === '新对话') {
    await waitFor(
      client,
      `document.querySelector('#requirement-input') instanceof HTMLTextAreaElement && !document.querySelector('#requirement-input').disabled && document.querySelector('.attachment-trigger') instanceof HTMLButtonElement && !document.querySelector('.attachment-trigger').disabled`,
      'isolated task draft readiness'
    )
    const composer = await evaluate(client, `(() => {
      const textarea = document.querySelector('#requirement-input')
      const send = document.querySelector('.send-button')
      const attachment = document.querySelector('.attachment-trigger')
      if (!(textarea instanceof HTMLTextAreaElement) || !(send instanceof HTMLButtonElement) || !(attachment instanceof HTMLButtonElement)) return null
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set
      setter?.call(textarea, '离线草稿输入验证')
      textarea.dispatchEvent(new Event('input', { bubbles: true }))
      attachment.click()
      return {
        textareaDisabled: textarea.disabled,
        sendDisabled: send.disabled,
        attachmentDisabled: attachment.disabled,
        text: document.querySelector('.composer-status')?.textContent ?? ''
      }
    })()`, 'exercise offline requirement composer')
    if (composer === null || composer.textareaDisabled || !composer.sendDisabled || composer.attachmentDisabled) {
      throw new Error(`Offline composer gating is incorrect: ${JSON.stringify(composer)}`)
    }
    if (!composer.text.includes('附件直接引用原位置')) {
      throw new Error(`Offline composer did not explain draft availability: ${JSON.stringify(composer)}`)
    }
    const progress = await evaluate(client, `(() => {
      const details = document.querySelector('.conversation-progress')
      const summary = details?.querySelector('summary')
      if (!(details instanceof HTMLDetailsElement) || !(summary instanceof HTMLElement)) return null
      const collapsed = summary.textContent?.replace(/\s+/g, ' ').trim() ?? ''
      details.open = true
      const rows = details.querySelectorAll('.conversation-progress-list button').length
      details.open = false
      return { collapsed, rows }
    })()`, 'inspect conversation task progress')
    if (progress === null || !progress.collapsed.includes('第 2 / 5 步') || progress.rows !== 5) {
      throw new Error(`Conversation task progress is incomplete: ${JSON.stringify(progress)}`)
    }
    await waitFor(client, `(() => {
      const button = [...document.querySelectorAll('.primary-nav button')].find((item) => item.textContent?.includes('集成与验证'))
      return button instanceof HTMLButtonElement && !button.disabled
    })()`, 'validation navigation to follow explicit task intent')
    await waitFor(client, `document.querySelector('.attachment-menu') !== null`, 'attachment menu')
    const menuLabels = await evaluate(client, `([...document.querySelectorAll('.attachment-menu strong')].map((node) => node.textContent?.trim()))`, 'read attachment menu labels')
    if (!menuLabels.includes('添加文件') || !menuLabels.includes('添加文件夹')) {
      throw new Error(`Attachment menu is incomplete: ${JSON.stringify(menuLabels)}`)
    }
    const chatMenuLabels = await evaluate(client, `(() => {
      const summary = document.querySelector('.chat-actions summary')
      if (!(summary instanceof HTMLElement)) return []
      summary.click()
      return [...document.querySelectorAll('.chat-actions button strong')].map((node) => node.textContent?.trim())
    })()`, 'open chat actions menu')
    if (!chatMenuLabels.includes('搜索聊天记录') || !chatMenuLabels.includes('导出聊天记录')) {
      throw new Error(`Chat actions menu is incomplete: ${JSON.stringify(chatMenuLabels)}`)
    }
    await evaluate(client, `document.querySelector('.chat-actions')?.removeAttribute('open')`, 'close chat actions menu')
    const connectivity = await evaluate(client, `(() => {
      const buttons = [...document.querySelectorAll('.connectivity-switch button')]
      const online = buttons.find((button) => button.textContent?.trim() === '联网')
      if (!(online instanceof HTMLButtonElement)) return false
      online.click()
      return true
    })()`, 'select online mode')
    if (!connectivity) {
      throw new Error(`Online mode gating is incorrect: ${JSON.stringify(connectivity)}`)
    }
    await waitFor(client, `[...document.querySelectorAll('.connectivity-switch button')].some((button) => button.textContent?.trim() === '联网' && button.getAttribute('aria-checked') === 'true') && document.querySelector('.send-button')?.disabled === true`, 'online mode gating')
    await evaluate(client, `([...document.querySelectorAll('.connectivity-switch button')].find((button) => button.textContent?.trim() === '本地'))?.click()`, 'restore local mode')
    await waitFor(client, `document.querySelector('#requirement-input')?.value === '离线草稿输入验证'`, 'offline draft input')
    const divider = await evaluate(client, `(() => {
      const handle = document.querySelector('.sidebar-resizer')
      const sidebar = document.querySelector('.sidebar')
      if (!(handle instanceof HTMLElement) || !(sidebar instanceof HTMLElement)) return null
      const handleRect = handle.getBoundingClientRect()
      return { x: handleRect.left + handleRect.width / 2, y: 260, width: sidebar.getBoundingClientRect().width }
    })()`, 'read sidebar divider')
    if (divider === null) throw new Error('Sidebar resize handle is missing')
    const targetX = Math.min(400, divider.x + 40)
    await client.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: divider.x, y: divider.y })
    await client.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: divider.x, y: divider.y, button: 'left', buttons: 1, clickCount: 1 })
    await client.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: targetX, y: divider.y, button: 'left', buttons: 1 })
    await client.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: targetX, y: divider.y, button: 'left', buttons: 0, clickCount: 1 })
    await waitFor(client, `document.querySelector('.sidebar')?.getBoundingClientRect().width >= ${divider.width + 24}`, 'resized sidebar width')
  }
  if (navigation === '执行中心') {
    await waitFor(client, `document.body.innerText.includes('没有真实 Worker 投影') || document.querySelectorAll('.worker-card').length > 0`, 'an honest execution projection')
  }
  if (navigation === '依赖关系') {
    const clicked = await evaluate(client, `(() => {
      const node = document.querySelector('g.dependency-node')
      if (!(node instanceof SVGElement)) return false
      node.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }))
      return true
    })()`, 'open a dependency node')
    if (!clicked) throw new Error('The dependency graph did not expose a clickable task node.')
    await waitFor(client, `document.querySelector('.graph-inspector')?.innerText.includes('任务节点')`, 'dependency inspector')
  }
  if (navigation === '成本') {
    await waitFor(client, `document.querySelector('.budget-hero') !== null && document.body.innerText.includes('本项目已发生成本')`, 'cost budget content')
  }
  if (navigation === '研发团队') {
    const roster = await evaluate(client, `(() => ({
      cards: document.querySelectorAll('.role-card').length,
      labels: [...document.querySelectorAll('.role-card h2')].map((node) => node.textContent?.trim()),
      unconfigured: document.querySelectorAll('.role-card.unconfigured').length
    }))()`, 'audit role roster')
    if (roster.cards !== 11 || !roster.labels.includes('产品需求经理') || !roster.labels.includes('安全守护')) {
      throw new Error(`Role roster is incomplete: ${JSON.stringify(roster)}`)
    }
  }
}

async function terminateChild(child) {
  if (child.exitCode !== null || child.signalCode !== null) return
  child.kill()
  const exited = await Promise.race([
    new Promise((resolveExit) => child.once('exit', () => resolveExit(true))),
    delay(2_500).then(() => false)
  ])
  if (exited) return
  if (process.platform === 'win32' && child.pid !== undefined) {
    const killer = spawn('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], {
      stdio: 'ignore',
      windowsHide: true
    })
    await new Promise((resolveExit) => killer.once('exit', resolveExit))
    return
  }
  child.kill('SIGKILL')
}

async function main() {
  await assertBuildExists()
  await mkdir(outputDirectory, { recursive: true })
  const userDataDirectory = await mkdtemp(join(tmpdir(), 'codentum-screenshot-profile-'))
  const port = await reservePort()
  const childOutput = []
  const environment = { ...process.env }
  delete environment.ELECTRON_RUN_AS_NODE
  delete environment.ELECTRON_RENDERER_URL
  environment.ELECTRON_DISABLE_SECURITY_WARNINGS = 'true'
  environment.CODENTUM_ENABLE_FIXTURES = '1'

  const child = spawn(electronExecutable, [
    `--remote-debugging-port=${port}`,
    '--remote-allow-origins=*',
    '--disable-gpu',
    `--user-data-dir=${userDataDirectory}`,
    desktopRoot
  ], {
    cwd: desktopRoot,
    env: environment,
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true
  })
  child.stdout.setEncoding('utf8')
  child.stderr.setEncoding('utf8')
  child.stdout.on('data', (chunk) => childOutput.push(`[stdout] ${chunk}`))
  child.stderr.on('data', (chunk) => childOutput.push(`[stderr] ${chunk}`))

  let client
  let monitor
  try {
    const target = await waitForDevToolsTarget(port, child)
    client = new DevToolsClient(target.webSocketDebuggerUrl)
    await client.connect()
    monitor = await startPageErrorMonitor(client)
    await Promise.all([
      client.send('Log.enable'),
      client.send('Page.enable'),
      client.send('Runtime.enable')
    ])

    await waitFor(client, `typeof window.codentum === 'object' && typeof window.codentum.listSources === 'function'`, 'window.codentum preload bridge')
    await waitFor(client, `document.querySelector('.app-shell') !== null && document.body.innerText.includes('Codentum')`, 'React application shell')

    const fixtureAudit = await evaluate(client, `(async () => {
      const sources = await window.codentum.listSources()
      const fixtureIds = sources.filter((source) => source.kind === 'fixture').map((source) => source.id).sort()
      const snapshots = await Promise.all(fixtureIds.map((id) => window.codentum.readSnapshot(id)))
      const options = [...document.querySelectorAll('select[aria-label="状态来源"] option')].map((option) => option.value).sort()
      return { fixtureIds, snapshotIds: snapshots.map((snapshot) => snapshot.source.id).sort(), options }
    })()`, 'audit fixture bridge')
    for (const key of ['fixtureIds', 'snapshotIds']) {
      if (JSON.stringify(fixtureAudit[key]) !== JSON.stringify(FIXTURE_IDS)) {
        throw new Error(`Expected all three fixture sources in ${key}; received ${JSON.stringify(fixtureAudit[key])}.`)
      }
    }
    for (const fixtureId of FIXTURE_IDS) {
      if (!fixtureAudit.options.includes(fixtureId)) throw new Error(`Fixture is missing from the visible source selector: ${fixtureId}`)
    }

    await clickNavigation(client, '新对话', '要做什么软件？')
    for (const fixtureId of FIXTURE_IDS) await selectFixture(client, fixtureId)
    await selectFixture(client, 'fixture:mid-flight')
    await waitFor(client, `document.querySelector('.fixture-badge')?.textContent?.includes('演示快照')`, 'fixture disclosure badge')

    for (const screenshot of SCREENSHOTS) {
      await clickNavigation(client, screenshot.navigation, screenshot.heading)
      await exerciseInteractiveDetails(client, screenshot.navigation)
      await monitor.assertClean(screenshot.navigation)
      await captureScreenshot(client, screenshot.name)
    }
    await monitor.assertClean('completed smoke test')
    process.stdout.write(`verified fixture sources: ${FIXTURE_IDS.join(', ')}\n`)
    process.stdout.write(`screenshot smoke passed: ${outputDirectory}\n`)
  } catch (error) {
    const diagnostics = childOutput.join('').trim()
    if (diagnostics.length > 0) process.stderr.write(`Electron output:\n${diagnostics.slice(-8_000)}\n`)
    throw error
  } finally {
    monitor?.stop()
    if (client !== undefined) {
      await client.send('Browser.close').catch(() => {})
      client.close()
    }
    await terminateChild(child)
    await rm(userDataDirectory, { force: true, recursive: true })
  }
}

main().catch((error) => {
  process.stderr.write(`${formatError(error)}\n`)
  process.exitCode = 1
})
