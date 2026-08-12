import { execFile } from 'node:child_process'
import { cp, mkdir, mkdtemp, realpath, rename, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterEach, describe, expect, it } from 'vitest'
import { ProjectPathError, ProjectStateSource, StateHub } from './index'

const dataDirectory = dirname(fileURLToPath(import.meta.url))
const fixtureRoot = resolve(dataDirectory, '../../../fixtures/golden-state')
const temporaryDirectories: string[] = []

afterEach(async () => {
  await Promise.all(temporaryDirectories.splice(0).map((path) => rm(path, { recursive: true, force: true })))
})

describe('FixtureStateSource', () => {
  it('can keep demo fixtures out of the product source list', () => {
    const hub = new StateHub({ fixtureRoot: null })
    expect(hub.listSources()).toEqual([])
    hub.close()
  })

  it.each([
    ['empty', 0, 0],
    ['mid-flight', 5, 3],
    ['blocked', 5, 2]
  ] as const)('reads the real %s golden state', async (name, packetCount, lockCount) => {
    const hub = new StateHub({ fixtureRoot })
    const snapshot = await hub.read(`fixture:${name}`)

    expect(snapshot.source.kind).toBe('fixture')
    expect(snapshot.packets).toHaveLength(packetCount)
    expect(snapshot.graph?.ownership.locks).toHaveLength(lockCount)
    expect(snapshot.workers).toEqual([])
    expect(snapshot.warnings).toEqual([])
    hub.close()
  })
})

describe('ProjectStateSource', () => {
  it('projects B worker evidence without inventing agents from packets', async () => {
    const project = await copyFixtureProject('empty')
    const workerDirectory = join(project, '.codentum', 'evidence', 'wp-real-attempt-1')
    await mkdir(workerDirectory, { recursive: true })
    const now = new Date().toISOString()
    await writeFile(
      join(workerDirectory, 'manifest.json'),
      JSON.stringify({
        worker_id: 'wp-real-attempt-1',
        packet_id: 'wp-real',
        role: 'coder',
        attempt: 1,
        workspace: project,
        tools: [],
        mounts: [],
        created_at: now
      }),
      'utf8'
    )
    await writeFile(
      join(workerDirectory, 'events.jsonl'),
      [
        JSON.stringify({ kind: 'started', at: now, seq: 1, payload: { workspace: project } }),
        JSON.stringify({ kind: 'progress', at: now, seq: 2, payload: { module_id: 'implementation' } }),
        JSON.stringify({ kind: 'cost', at: now, seq: 3, payload: { spent_cny: 0.42 } })
      ].join('\n') + '\n',
      'utf8'
    )

    const source = await ProjectStateSource.create(project)
    const snapshot = await source.read()

    expect(snapshot.packets).toEqual([])
    expect(snapshot.workers).toEqual([
      expect.objectContaining({
        workerId: 'wp-real-attempt-1',
        packetId: 'wp-real',
        role: 'coder',
        state: 'running',
        currentModule: 'implementation',
        spentCny: 0.42
      })
    ])
    source.close()
  })

  it('projects B AgentTeams status events through the existing worker stream', async () => {
    const project = await copyFixtureProject('empty')
    const workerDirectory = join(project, '.codentum', 'evidence', 'wp-team-attempt-1')
    await mkdir(workerDirectory, { recursive: true })
    const now = new Date().toISOString()
    await writeFile(
      join(workerDirectory, 'manifest.json'),
      JSON.stringify({
        worker_id: 'wp-team-attempt-1',
        packet_id: 'wp-team',
        role: 'coder',
        attempt: 1,
        workspace: project,
        tools: [],
        mounts: [],
        created_at: now,
        runtime_mode: 'agentteams',
        agentteams: {
          worker: 'codentum-coder-wp-team-a1',
          runtime: 'copaw',
          model: 'qwen3.6-plus'
        }
      }),
      'utf8'
    )
    await writeFile(
      join(workerDirectory, 'events.jsonl'),
      [
        JSON.stringify({ kind: 'started', at: now, seq: 1, payload: { runtime_mode: 'agentteams', workspace: project } }),
        JSON.stringify({ kind: 'checkpoint', at: now, seq: 2, payload: { path: 'checkpoints/0000.json' } }),
        JSON.stringify({
          kind: 'progress',
          at: now,
          seq: 3,
          payload: {
            runtime_mode: 'agentteams',
            moduleId: 'agentteams.worker',
            moduleLabel: 'AgentTeams Worker',
            moduleState: 'running',
            agentteams_worker: 'codentum-coder-wp-team-a1',
            phase: 'Running',
            container_state: 'running',
            status_ref: 'file:agentteams/status.json'
          }
        })
      ].join('\n') + '\n',
      'utf8'
    )

    const source = await ProjectStateSource.create(project)
    const snapshot = await source.read()

    expect(snapshot.workers).toEqual([
      expect.objectContaining({
        workerId: 'wp-team-attempt-1',
        packetId: 'wp-team',
        role: 'coder',
        state: 'running',
        currentModule: 'agentteams.worker'
      })
    ])
    expect(snapshot.workers[0]?.events.at(-1)?.payload).toEqual(expect.objectContaining({
      agentteams_worker: 'codentum-coder-wp-team-a1',
      phase: 'Running',
      status_ref: 'file:agentteams/status.json'
    }))
    source.close()
  })

  it('projects B worker evidence from an isolated linked worktree', async () => {
    const project = await copyFixtureProject('empty')
    await runGit(project, ['init'])
    await runGit(project, ['config', 'user.email', 'desktop-test@codentum.invalid'])
    await runGit(project, ['config', 'user.name', 'Codentum Desktop Test'])
    await runGit(project, ['add', '.'])
    await runGit(project, ['commit', '-m', 'fixture state'])

    const worktreeParent = await mkdtemp(join(tmpdir(), 'codentum-worker-parent-'))
    temporaryDirectories.push(worktreeParent)
    const worktree = join(worktreeParent, 'worker-attempt')
    await runGit(project, ['worktree', 'add', '--detach', worktree, 'HEAD'])
    const workerDirectory = join(worktree, '.codentum', 'evidence', 'wp-linked-attempt-1')
    await mkdir(workerDirectory, { recursive: true })
    const now = new Date().toISOString()
    const runtimeWorkspace = worktree.replaceAll('\\', '/')
    await writeFile(join(workerDirectory, 'manifest.json'), JSON.stringify({
      worker_id: 'wp-linked-attempt-1',
      packet_id: 'wp-linked',
      role: 'coder',
      attempt: 1,
      workspace: runtimeWorkspace,
      created_at: now
    }), 'utf8')
    await writeFile(join(workerDirectory, 'events.jsonl'), [
      JSON.stringify({ kind: 'started', at: now, seq: 1, payload: { workspace: runtimeWorkspace } }),
      JSON.stringify({ kind: 'checkpoint', at: now, seq: 2, payload: { path: 'checkpoints/0000.json' } })
    ].join('\n') + '\n', 'utf8')

    const source = await ProjectStateSource.create(project)
    const snapshot = await source.read()

    expect(snapshot.workers).toEqual([
      expect.objectContaining({
        workerId: 'wp-linked-attempt-1',
        packetId: 'wp-linked',
        workspace: runtimeWorkspace,
        state: 'running'
      })
    ])
    source.close()
  })

  it('subscribes to an atomically replaced project state file', async () => {
    const project = await copyFixtureProject('empty')
    const hub = new StateHub({ fixtureRoot, pollIntervalMs: 25 })
    const descriptor = await hub.selectProject(project)

    const changed = new Promise<number>((resolveChanged, reject) => {
      const timeout = setTimeout(() => reject(new Error('project watch timed out')), 3_000)
      const unsubscribe = hub.watch(descriptor.id, (snapshot) => {
        if (snapshot.budget?.spentCny === 1.25) {
          clearTimeout(timeout)
          unsubscribe()
          resolveChanged(snapshot.budget.spentCny)
        }
      })
    })

    const budgetPath = join(project, '.codentum', 'budget.json')
    const temporaryBudgetPath = `${budgetPath}.tmp`
    await writeFile(
      temporaryBudgetPath,
      JSON.stringify({
        schemaVersion: 1,
        currency: 'CNY',
        limitCny: 20,
        spentCny: 1.25,
        byRole: { coder: 1.25 },
        byModel: {},
        degradationChain: ['drop_semantic_memory'],
        alerts: []
      }),
      'utf8'
    )
    await rename(temporaryBudgetPath, budgetPath)

    await expect(changed).resolves.toBe(1.25)
    hub.close()
  })

  it('keeps the same project snapshot on partial JSON and reports it explicitly', async () => {
    const project = await copyFixtureProject('empty')
    const source = await ProjectStateSource.create(project)
    await source.read()
    await writeFile(join(project, '.codentum', 'graph.json'), '{"schemaVersion":', 'utf8')

    const snapshot = await source.read()

    expect(snapshot.source.kind).toBe('project')
    expect(snapshot.packets).toEqual([])
    expect(snapshot.graph?.dependency.nodes).toEqual([])
    expect(snapshot.warnings.join('\n')).toContain('[partial-write]')
    expect(snapshot.warnings.join('\n')).toContain('[stale]')
    source.close()
  })

  it('keeps the last coherent project snapshot when the state directory disappears', async () => {
    const project = await copyFixtureProject('empty')
    const source = await ProjectStateSource.create(project)
    const initial = await source.read()
    await rm(join(project, '.codentum'), { recursive: true })

    const snapshot = await source.read()

    expect(snapshot.revision).toBe(initial.revision)
    expect(snapshot.warnings.join('\n')).toContain('[missing] State directory is unavailable')
    expect(snapshot.warnings.join('\n')).toContain('[stale]')
    source.close()
  })

  it('distinguishes stable bad JSON from an incomplete write', async () => {
    const project = await copyFixtureProject('empty')
    const source = await ProjectStateSource.create(project)
    await source.read()
    await writeFile(join(project, '.codentum', 'budget.json'), '{"schemaVersion": 1, nope}', 'utf8')

    const snapshot = await source.read()

    expect(snapshot.warnings.join('\n')).toContain('[bad-json]')
    expect(snapshot.warnings.join('\n')).toContain('[stale]')
    source.close()
  })

  it('refuses an oversized untrusted state file instead of loading it into memory', async () => {
    const project = await copyFixtureProject('empty')
    const source = await ProjectStateSource.create(project)
    await writeFile(join(project, '.codentum', 'knowledge.json'), 'x'.repeat(4 * 1024 * 1024 + 1), 'utf8')

    const snapshot = await source.read()

    expect(snapshot.knowledge).toBeNull()
    expect(snapshot.warnings.join('\n')).toContain('[limit] Ignored state file larger than')
    source.close()
  })

  it('marks an active worker with an old event stream as stale', async () => {
    const project = await copyFixtureProject('empty')
    const workerDirectory = join(project, '.codentum', 'evidence', 'stale-worker')
    await mkdir(workerDirectory, { recursive: true })
    const old = '2020-01-01T00:00:00.000Z'
    await writeFile(
      join(workerDirectory, 'manifest.json'),
      JSON.stringify({
        worker_id: 'stale-worker',
        packet_id: 'wp-stale',
        role: 'coder',
        attempt: 1,
        workspace: project,
        created_at: old
      }),
      'utf8'
    )
    await writeFile(
      join(workerDirectory, 'events.jsonl'),
      JSON.stringify({ kind: 'started', at: old, seq: 1, payload: { workspace: project } }) + '\n',
      'utf8'
    )

    const source = await ProjectStateSource.create(project, { staleAfterMs: 1_000 })
    const snapshot = await source.read()

    expect(snapshot.workers[0]?.state).toBe('running')
    expect(snapshot.warnings.join('\n')).toContain('[stale] Worker stale-worker')
    source.close()
  })

  it('accepts a project without .codentum as an uninitialized empty source', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'codentum-no-state-'))
    temporaryDirectories.push(directory)

    const source = await ProjectStateSource.create(directory)
    const snapshot = await source.read()

    await expect(realpath(directory)).resolves.toBe(snapshot.source.rootPath)
    expect(snapshot.packets).toEqual([])
    expect(snapshot.workers).toEqual([])
    expect(snapshot.warnings.join('\n')).toContain('[missing] State directory is unavailable')
    source.close()
  })

  it('rejects paths that are not valid project roots', async () => {
    await expect(ProjectStateSource.create('relative/project')).rejects.toBeInstanceOf(ProjectPathError)
    const directory = await mkdtemp(join(tmpdir(), 'codentum-invalid-state-'))
    temporaryDirectories.push(directory)
    await writeFile(join(directory, '.codentum'), 'not a directory', 'utf8')
    await expect(ProjectStateSource.create(directory)).rejects.toMatchObject({
      code: 'STATE_DIRECTORY_NOT_DIRECTORY'
    })
  })
})

async function copyFixtureProject(name: string): Promise<string> {
  const project = await mkdtemp(join(tmpdir(), `codentum-project-${name}-`))
  temporaryDirectories.push(project)
  await cp(join(fixtureRoot, name, '.codentum'), join(project, '.codentum'), { recursive: true })
  return project
}

function runGit(root: string, args: readonly string[]): Promise<void> {
  return new Promise((resolveRun, rejectRun) => {
    execFile('git', ['-C', root, ...args], { encoding: 'utf8', windowsHide: true }, (error) => {
      if (error === null) resolveRun()
      else rejectRun(error)
    })
  })
}

describe('StateHub.projectRoot 必须认得草稿作用域', () => {
  // ★ 2026-08-11 用真引擎驱动桌面端时撞出来的：从界面提交需求必然抛
  //   `Unknown state source: project:<hash>:task:<uuid>`。
  //
  //   链路是这样断的：
  //     renderer  taskDraftScope() → `${sourceId}:task:${taskId}`
  //     main      stateHub.projectRoot(draftScope)
  //     hub       this.sources.get(draftScope)   ← 表是按 sourceId 建的
  //
  //   `project:<24hex>` 与 `project:<24hex>:task:<uuid>` 永远不相等，
  //   于是**每一次界面提交都会失败**。
  //
  //   ★ 为什么没被测出来：`RequirementComposer` 没有任何测试文件 import
  //     （08-10 核对 C 的任务书时已经记过这条），而 main 侧的
  //     `requirement-draft-store.test.ts` 直接给的是裸 scopeId，
  //     绕开了「scope 与 sourceId 不是同一个字符串」这件事。

  it('传入 sourceId 时返回项目根', async () => {
    const project = await copyFixtureProject('empty')
    const hub = new StateHub({ fixtureRoot: null })
    const descriptor = await hub.selectProject(project)
    expect(hub.projectRoot(descriptor.id)).toBe(descriptor.rootPath)
    hub.close()
  })

  it('★ 传入 `<sourceId>:task:<uuid>` 形式的草稿作用域时也要认 —— 修复前必红', async () => {
    const project = await copyFixtureProject('empty')
    const hub = new StateHub({ fixtureRoot: null })
    const descriptor = await hub.selectProject(project)
    const draftScope = `${descriptor.id}:task:11111111-2222-3333-4444-555555555555`
    expect(hub.projectRoot(draftScope)).toBe(descriptor.rootPath)
    hub.close()
  })

  it('真正不存在的源仍要报错，不能被前缀解析吞掉', async () => {
    const hub = new StateHub({ fixtureRoot: null })
    expect(() => hub.projectRoot('project:deadbeefdeadbeefdeadbeef:task:x')).toThrow(
      /Unknown state source/u
    )
    hub.close()
  })
})

describe('StateHub 可以注册引擎握手里的项目根', () => {
  it('selectProject(projectRoot) 后 listSources 立即出现项目来源', async () => {
    const project = await copyFixtureProject('empty')
    const hub = new StateHub({ fixtureRoot: null })

    const descriptor = await hub.selectProject(project)

    expect(hub.listSources()).toEqual([descriptor])
    expect(descriptor.kind).toBe('project')
    expect(descriptor.rootPath).toBe(await realpath(project))
    hub.close()
  })
})
