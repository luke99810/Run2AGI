import { describe, expect, it } from 'vitest'
import { execFileSync } from 'node:child_process'
import { mkdtemp } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { SidecarManager } from './SidecarManager'

/**
 * ★ 唯一一条「桌面端 ↔ 真引擎」的绑定测试。
 *
 * 2026-08-11 实机反复撞到「一打开文件夹引擎就断了」，而所有既有测试都是绿的 ——
 * 因为它们要么用假 sidecar（`SidecarManager.test.ts` 里那段内联 node 脚本），
 * 要么跑在 `CODENTUM_ENABLE_FIXTURES=1` 下（截图脚本）。
 * **`bindProject()` 这条路径从来没有对着真引擎跑过一次。**
 *
 * 这条测试补的就是那个缺口：真 sidecar + 真引擎，走完
 * `start()` → `bindProject()`，并断言引擎确实绑到了被选中的项目上。
 *
 * ★ 不需要模型 Key：没有 Key 时引擎仍然 connected，只是
 *   `capabilities.requirements` 报 false。这里断言的是**绑定**，不是执行。
 */

const REPO = resolve(__dirname, '..', '..', '..', '..', '..')

function pythonAvailable(): boolean {
  for (const [exe, ...prefix] of [['py', '-3.11'], ['python3.11'], ['python']]) {
    try {
      execFileSync(exe, [...prefix, '-c', 'import sys; raise SystemExit(sys.version_info < (3, 11))'], {
        stdio: 'ignore'
      })
      return true
    } catch { /* 试下一个 */ }
  }
  return false
}

describe('SidecarManager 与真引擎的绑定', () => {
  it.skipIf(!pythonAvailable())(
    'bindProject 之后引擎必须绑到被选中的项目上，而不是启动时写死的那个',
    async () => {
      const selected = await mkdtemp(join(tmpdir(), 'codentum-selected-'))
      const pinned = await mkdtemp(join(tmpdir(), 'codentum-pinned-'))

      const saved = { ...process.env }
      process.env['PYTHONPATH'] = [
        'packages/contracts/python', 'packages/control-plane', 'packages/harness',
        'packages/roles', 'packages/delivery', 'packages/engine'
      ].map((p) => resolve(REPO, p)).join(';')
      process.env['PYTHONIOENCODING'] = 'utf-8'
      // ★ 启动时写死 pinned —— 这正是 CODENTUM_ENGINE_COMMAND_JSON 的现实：
      //   它在 Electron 启动那一刻就定了，之后用户选任何项目它都不会变。
      process.env['CODENTUM_ENGINE_COMMAND_JSON'] = JSON.stringify([
        'python', '-m', 'codentum_engine', '--project-root', pinned, '--log-level', 'WARNING'
      ])
      delete process.env['CODENTUM_SIDECAR_EXECUTABLE']
      delete process.env['CODENTUM_SIDECAR_ARGS_JSON']
      delete process.env['CODENTUM_PROJECT_ROOT']

      const manager = new SidecarManager({
        isPackaged: false,
        getAppPath: () => resolve(REPO, 'packages', 'desktop', 'out')
      })

      try {
        const started = await manager.start()
        expect(started.connected, `start() 就没连上：${started.unavailableReason ?? ''}`).toBe(true)

        const bound = await manager.bindProject(selected)
        expect(bound.connected, `bindProject() 之后断开了：${bound.unavailableReason ?? ''}`).toBe(true)
        expect(bound.projectRoot?.toLowerCase()).toBe(selected.toLowerCase())
      } finally {
        await manager.close().catch(() => undefined)
        for (const key of Object.keys(process.env)) if (!(key in saved)) delete process.env[key]
        Object.assign(process.env, saved)
      }
    },
    120_000
  )
})
