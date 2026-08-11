import { afterEach, describe, expect, it } from 'vitest'
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { resolveSidecarLaunch, SidecarManager } from './SidecarManager'

const originalExecutable = process.env['CODENTUM_SIDECAR_EXECUTABLE']
const originalArgs = process.env['CODENTUM_SIDECAR_ARGS_JSON']

afterEach(() => {
  if (originalExecutable === undefined) delete process.env['CODENTUM_SIDECAR_EXECUTABLE']
  else process.env['CODENTUM_SIDECAR_EXECUTABLE'] = originalExecutable
  if (originalArgs === undefined) delete process.env['CODENTUM_SIDECAR_ARGS_JSON']
  else process.env['CODENTUM_SIDECAR_ARGS_JSON'] = originalArgs
})

describe('resolveSidecarLaunch', () => {
  it('uses an explicit executable without shell parsing', () => {
    process.env['CODENTUM_SIDECAR_EXECUTABLE'] = 'C:\\Program Files\\Codentum\\sidecar.exe'
    process.env['CODENTUM_SIDECAR_ARGS_JSON'] = '["--safe","中文"]'
    expect(resolveSidecarLaunch({ isPackaged: false, getAppPath: () => 'unused' })).toEqual({
      executable: 'C:\\Program Files\\Codentum\\sidecar.exe',
      args: ['--safe', '中文']
    })
  })

  it('rejects a command string disguised as args', () => {
    process.env['CODENTUM_SIDECAR_EXECUTABLE'] = 'sidecar.exe'
    process.env['CODENTUM_SIDECAR_ARGS_JSON'] = '--unsafe shell text'
    expect(() => resolveSidecarLaunch({ isPackaged: false, getAppPath: () => 'unused' })).toThrow()
  })

  describe('开发态定位 sidecar.py：不能假设 getAppPath() 就是 packages/desktop', () => {
    // ★ 这组测试是 2026-08-11 用**真引擎 + 构建产物**启动桌面端时撞出来的：
    //   握手报 "Development sidecar is missing:
    //   packages/desktop/out/delivery/codentum_delivery/sidecar.py"。
    //
    //   原实现是 resolve(getAppPath(), '..', 'delivery', ...)，硬编码了
    //   「getAppPath() 一定是 packages/desktop」。这只在 electron-vite dev 下成立。
    //
    //   ★ 为什么此前没被发现：screenshot-smoke.mjs 一直带
    //   CODENTUM_ENABLE_FIXTURES=1 跑，**假数据模式根本不需要 sidecar** ——
    //   这条路径从来没被真正走过一次。

    const created: string[] = []

    afterEach(async () => {
      await Promise.all(created.splice(0).map((dir) => rm(dir, { recursive: true, force: true })))
    })

    async function fakeRepo(): Promise<string> {
      const root = await mkdtemp(join(tmpdir(), 'codentum-repo-'))
      created.push(root)
      await mkdir(resolve(root, 'packages', 'delivery', 'codentum_delivery'), { recursive: true })
      await writeFile(resolve(root, 'packages', 'delivery', 'codentum_delivery', 'sidecar.py'), '', 'utf8')
      await mkdir(resolve(root, 'packages', 'desktop', 'out', 'main'), { recursive: true })
      return root
    }

    it('electron-vite dev 布局：getAppPath() = packages/desktop', async () => {
      const root = await fakeRepo()
      const launch = resolveSidecarLaunch({
        isPackaged: false,
        getAppPath: () => resolve(root, 'packages', 'desktop')
      })
      expect(launch.args.at(-1)).toBe(
        resolve(root, 'packages', 'delivery', 'codentum_delivery', 'sidecar.py')
      )
      expect(launch.cwd).toBe(root)
    })

    it('★ 构建产物布局：getAppPath() = packages/desktop/out —— 修复前这条必红', async () => {
      const root = await fakeRepo()
      const launch = resolveSidecarLaunch({
        isPackaged: false,
        getAppPath: () => resolve(root, 'packages', 'desktop', 'out')
      })
      expect(launch.args.at(-1)).toBe(
        resolve(root, 'packages', 'delivery', 'codentum_delivery', 'sidecar.py')
      )
      // ★ cwd 也必须是仓库根，不是 out/ 的上一级 —— 原实现里它同样写死了 '../..'
      expect(launch.cwd).toBe(root)
    })

    it('确实找不到时要报清楚是从哪儿开始找的，而不是丢一个拼出来的假路径', async () => {
      const bare = await mkdtemp(join(tmpdir(), 'codentum-bare-'))
      created.push(bare)
      expect(() => resolveSidecarLaunch({ isPackaged: false, getAppPath: () => bare })).toThrow(
        /逐级向上都没找到/u
      )
    })
  })

  it('shares the in-flight startup handshake instead of returning a stale unavailable value', async () => {
    const child = `
      const readline = require('node:readline');
      const capabilities = Object.fromEntries([
        'requirements','planConfirmation','pauseAtSafePoint','resume','stop',
        'keepMemory','forkFromCheckpoint','appendPrompt','insertModule'
      ].map((key) => [key, false]));
      capabilities.stop = true;
      const lines = readline.createInterface({ input: process.stdin });
      const send = (value) => process.stdout.write(JSON.stringify(value) + '\\n');
      lines.on('line', (line) => {
        const request = JSON.parse(line);
        if (request.method === 'handshake') {
          setTimeout(() => send({ id: request.id, ok: true, result: {
            connected: true, protocolVersion: 1, engineVersion: 'test-engine',
            stateRevision: 3, runId: 'run-1', projectRoot: process.cwd(), capabilities
          }}), 80);
        } else if (request.method === 'shutdown') {
          send({ id: request.id, ok: true, result: { stopped: true } });
          setTimeout(() => process.exit(0), 10);
        } else if (request.method === 'command') {
          const command = request.params.command;
          send({ id: request.id, ok: true, result: {
            commandId: command.commandId, status: 'applied', stateRevision: 2,
            receivedAt: '2026-08-07T00:00:00.000Z'
          }});
        }
      });
    `
    process.env['CODENTUM_SIDECAR_EXECUTABLE'] = process.execPath
    process.env['CODENTUM_SIDECAR_ARGS_JSON'] = JSON.stringify(['-e', child])
    const manager = new SidecarManager({ isPackaged: false, getAppPath: () => 'unused' })
    try {
      const [started, concurrent] = await Promise.all([manager.start(), manager.handshake()])
      expect(started.connected).toBe(true)
      expect(concurrent).toEqual(started)
      expect(concurrent.stateRevision).toBe(3)
      await expect(manager.command({
        commandId: 'command-regression', runId: 'run-1', expectedRevision: 3,
        target: { agentId: 'worker-1' }, action: 'stop', payload: {},
        requestedAt: '2026-08-07T00:00:00.000Z'
      })).rejects.toThrow('non-monotonic')
    } finally {
      await manager.close()
    }
  })

  it('restarts the sidecar in each selected project directory', async () => {
    const child = `
      const readline = require('node:readline');
      const capabilities = Object.fromEntries([
        'requirements','planConfirmation','pauseAtSafePoint','resume','stop',
        'keepMemory','forkFromCheckpoint','appendPrompt','insertModule'
      ].map((key) => [key, false]));
      const lines = readline.createInterface({ input: process.stdin });
      const send = (value) => process.stdout.write(JSON.stringify(value) + '\\n');
      lines.on('line', (line) => {
        const request = JSON.parse(line);
        if (request.method === 'handshake') {
          send({ id: request.id, ok: true, result: {
            connected: true, protocolVersion: 1, engineVersion: 'cwd-test-engine',
            stateRevision: 0, runId: process.cwd(), projectRoot: process.cwd(), capabilities
          }});
        } else if (request.method === 'shutdown') {
          send({ id: request.id, ok: true, result: { stopped: true } });
          setTimeout(() => process.exit(0), 10);
        }
      });
    `
    process.env['CODENTUM_SIDECAR_EXECUTABLE'] = process.execPath
    process.env['CODENTUM_SIDECAR_ARGS_JSON'] = JSON.stringify(['-e', child])
    const first = await mkdtemp(join(tmpdir(), 'codentum-bind-first-'))
    const second = await mkdtemp(join(tmpdir(), 'codentum-bind-second-'))
    const manager = new SidecarManager({ isPackaged: false, getAppPath: () => 'unused' })
    try {
      const firstHandshake = await manager.bindProject(first)
      expect(firstHandshake.connected).toBe(true)
      expect(firstHandshake.projectRoot).toBe(resolve(first))

      const secondHandshake = await manager.bindProject(second)
      expect(secondHandshake.connected).toBe(true)
      expect(secondHandshake.projectRoot).toBe(resolve(second))
      expect(secondHandshake.runId).not.toBe(firstHandshake.runId)
    } finally {
      await manager.close()
      await Promise.all([rm(first, { recursive: true, force: true }), rm(second, { recursive: true, force: true })])
    }
  })
})
