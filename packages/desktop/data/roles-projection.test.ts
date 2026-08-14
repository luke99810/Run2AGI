import { afterEach, describe, expect, it } from 'vitest'
import { copyFile, mkdir, mkdtemp, readdir, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { ProjectStateSource } from './index'

/**
 * ★ 「研发团队」页的岗位必须来自**项目投影**，不是前端的静态清单。
 *
 * 2026-08-11 的实况：界面显示「系统岗位 11、项目投影 0」——
 * 名字对得上，但那 11 张卡片来自桌面端自己维护的静态列表，
 * **不代表这 11 个角色真的被系统加载了**。
 *
 * 真源是 B 的 `packages/roles/specs/*.json`，桌面端读的是项目内的
 * `.codentum/roles/`，两者之间原本没有任何人搬运。现由引擎（装配点）
 * 在启动时投影 —— 它是唯一同时知道「RoleSpec 从哪来」和「状态目录在哪」的地方。
 *
 * ★ 这条测试守的是**接缝**：B 写出来的格式，C 这边读不读得动。
 *   直接拿 B 的源文件喂给 C 的读取器，不做任何转换 ——
 *   一旦任何一侧改了形状，这里立刻红。
 *   （引擎写出来的内容与 B 源文件一致，由 engine 侧的测试守，
 *     两边各守一半，不引入跨语言 fixture 的同步负担。）
 */

const REPO = resolve(__dirname, '..', '..', '..')
const SPECS = resolve(REPO, 'packages', 'roles', 'specs')
const SKILLS = resolve(REPO, 'packages', 'roles', 'skills')
const MCP = resolve(REPO, 'packages', 'roles', 'mcp')
const EMPTY_FIXTURE = resolve(REPO, 'fixtures', 'golden-state', 'empty', '.codentum')

describe('RoleSpec 项目投影', () => {
  const created: string[] = []

  afterEach(async () => {
    await Promise.all(created.splice(0).map((dir) => rm(dir, { recursive: true, force: true })))
  })

  it('B 的 RoleSpec 原样放进 .codentum/roles/ 后，桌面端必须全部读出来', async () => {
    const project = await mkdtemp(join(tmpdir(), 'codentum-roles-'))
    created.push(project)
    const state = join(project, '.codentum')
    await mkdir(join(state, 'roles'), { recursive: true })

    // 借 golden-state/empty 补齐其余必需成员，避免快照被判 incoherent
    for (const member of ['graph.json', 'budget.json', 'decisions.jsonl']) {
      await copyFile(join(EMPTY_FIXTURE, member), join(state, member))
    }
    for (const directory of ['packets', 'evidence', 'knowledge']) {
      await mkdir(join(state, directory), { recursive: true })
    }

    const specs = (await readdir(SPECS)).filter((name) => name.endsWith('.json'))
    expect(specs.length, 'B 的 RoleSpec 源目录是空的？').toBeGreaterThanOrEqual(11)
    for (const name of specs) {
      await copyFile(join(SPECS, name), join(state, 'roles', name))
    }

    const source = await ProjectStateSource.create(project, {})
    try {
      const snapshot = await source.read()
      expect(
        snapshot.warnings,
        `读 roles/ 时报了警告，说明 B 写的形状 C 这边不认：${snapshot.warnings.join(' | ')}`
      ).toEqual([])
      expect(snapshot.roles.map((role) => role.id).sort()).toEqual(
        specs.map((name) => name.replace(/\.json$/u, '')).sort()
      )
      const skillsByRole = new Map(snapshot.roles.map((role) => [role.id, role.skills?.map((skill) => skill.id) ?? []]))
      expect(skillsByRole.get('coder')).toEqual(['frontend', 'backend', 'testing', 'debugging'])
      expect(skillsByRole.get('architect')).toEqual(['architecture', 'security'])
      expect(skillsByRole.get('planner')).toEqual(['planning', 'cost-governance'])
      expect(skillsByRole.get('qa')).toEqual(['testing'])
      expect(skillsByRole.get('reviewer')).toEqual(['review', 'security'])
      expect(skillsByRole.get('integrator')).toEqual(['integration', 'delivery', 'testing', 'review', 'debugging'])
    } finally {
      source.close()
    }
  })

  it('★ 形状不对的角色文件必须被拒，而不是悄悄读成一个空壳', async () => {
    const project = await mkdtemp(join(tmpdir(), 'codentum-roles-bad-'))
    created.push(project)
    const state = join(project, '.codentum')
    await mkdir(join(state, 'roles'), { recursive: true })
    for (const member of ['graph.json', 'budget.json', 'decisions.jsonl']) {
      await copyFile(join(EMPTY_FIXTURE, member), join(state, member))
    }
    for (const directory of ['packets', 'evidence', 'knowledge']) {
      await mkdir(join(state, directory), { recursive: true })
    }
    // 缺 usesModel / tools / transitions —— 只有一个 id
    await writeFile(join(state, 'roles', 'coder.json'), JSON.stringify({ id: 'coder' }), 'utf8')

    const source = await ProjectStateSource.create(project, {})
    try {
      const snapshot = await source.read()
      expect(snapshot.roles).toEqual([])
      expect(snapshot.warnings.length, '形状不对却一声不吭').toBeGreaterThan(0)
    } finally {
      source.close()
    }
  })

  it('B 的 MCP 清单原样放进 .codentum/mcp/ 后，桌面端必须读成运行时投影', async () => {
    const project = await mkdtemp(join(tmpdir(), 'codentum-mcp-'))
    created.push(project)
    const state = join(project, '.codentum')
    await mkdir(join(state, 'mcp'), { recursive: true })

    for (const member of ['graph.json', 'budget.json', 'decisions.jsonl']) {
      await copyFile(join(EMPTY_FIXTURE, member), join(state, member))
    }
    for (const directory of ['packets', 'evidence', 'knowledge', 'roles']) {
      await mkdir(join(state, directory), { recursive: true })
    }

    const services = (await readdir(MCP)).filter((name) => name.endsWith('.json'))
    for (const name of services) {
      await copyFile(join(MCP, name), join(state, 'mcp', name))
    }

    const source = await ProjectStateSource.create(project, {})
    try {
      const snapshot = await source.read()
      expect(snapshot.warnings).toEqual([])
      // ★ 守「四个基础服务都被读成投影」而非「恰好只有这四个」。
      //   第三方应用（github / feishu / sentry / …）会随需求增删，
      //   写死清单不会因为多一个文件而更有保障，只会让每次加应用都要改测试。
      //   —— 与 Python 侧 test_loader.py 的三处同一个修法。
      const ids = snapshot.mcpServices.map((service) => service.id)
      for (const required of ['agentteams', 'browser', 'filesystem', 'git']) {
        expect(ids).toContain(required)
      }
      expect(snapshot.mcpServices.find((service) => service.id === 'filesystem')?.tools).toEqual([
        'read_file',
        'write_file',
        'list_directory'
      ])
      expect(snapshot.mcpServices.find((service) => service.id === 'agentteams')?.authentication).toBe('missing')
      expect(snapshot.mcpServices.find((service) => service.id === 'github')).toMatchObject({
        category: 'third-party-app',
        purpose: expect.stringContaining('代码托管'),
        enabled: false,
        requiresEnv: ['GITHUB_PERSONAL_ACCESS_TOKEN']
      })
    } finally {
      source.close()
    }
  })

  it('B 的 Skill manifest 和 SKILL.md 原样投影后，C 必须读出完整可执行契约', async () => {
    const project = await mkdtemp(join(tmpdir(), 'codentum-skills-'))
    created.push(project)
    const state = join(project, '.codentum')
    const shared = join(state, 'skills', 'shared')
    await mkdir(shared, { recursive: true })

    for (const member of ['graph.json', 'budget.json', 'decisions.jsonl']) {
      await copyFile(join(EMPTY_FIXTURE, member), join(state, member))
    }
    for (const directory of ['packets', 'evidence', 'knowledge', 'roles', 'mcp']) {
      await mkdir(join(state, directory), { recursive: true })
    }

    const skillNames = (await readdir(SKILLS, { withFileTypes: true }))
      .filter((entry) => entry.isDirectory())
      .map((entry) => entry.name)
      .sort()
    expect(skillNames.length, 'B 的 Skill 源目录是空的？').toBeGreaterThan(0)
    for (const name of skillNames) {
      const target = join(shared, name)
      await mkdir(target, { recursive: true })
      await copyFile(join(SKILLS, name, 'manifest.json'), join(target, 'manifest.json'))
      await copyFile(join(SKILLS, name, 'SKILL.md'), join(target, 'SKILL.md'))
    }

    const source = await ProjectStateSource.create(project, {})
    try {
      const snapshot = await source.read()
      expect(snapshot.warnings).toEqual([])
      expect(snapshot.skills.map((skill) => skill.id).sort()).toEqual(skillNames)
      const frontend = snapshot.skills.find((skill) => skill.id === 'frontend')
      expect(frontend?.permissions.tools).toEqual(expect.arrayContaining(['read_file', 'write_file', 'run_tests', 'create_diff']))
      expect(frontend?.appliesTo).toEqual(['coder', 'helper'])
      expect(frontend?.inputs).toEqual(expect.any(Object))
      expect(frontend?.outputs).toEqual(expect.any(Object))
      expect(frontend?.preconditions).toEqual(expect.any(Array))
      expect(frontend?.failure.timeoutSeconds).toBeGreaterThan(0)
      expect(frontend?.permissions.networkAccess).toEqual(expect.any(Array))
      expect(frontend?.requiresSkills).toEqual(expect.any(Array))
      expect(frontend?.conflicts).toEqual(expect.any(Array))
      expect(frontend?.reuse).toEqual(expect.objectContaining({ crossRole: expect.any(Boolean), crossProject: expect.any(Boolean) }))
      expect(frontend?.instructionMarkdown).toContain('# Frontend Skill')
    } finally {
      source.close()
    }
  })

  it('把引擎需求记录中的 taskId 投影为本地任务与 Packet 的关联', async () => {
    const project = await mkdtemp(join(tmpdir(), 'codentum-requirements-'))
    created.push(project)
    const state = join(project, '.codentum')
    await mkdir(join(state, 'requirements'), { recursive: true })
    for (const member of ['graph.json', 'budget.json', 'decisions.jsonl']) {
      await copyFile(join(EMPTY_FIXTURE, member), join(state, member))
    }
    for (const directory of ['packets', 'evidence', 'knowledge', 'roles', 'mcp']) {
      await mkdir(join(state, directory), { recursive: true })
    }
    await writeFile(join(state, 'requirements', 'wp-demo.json'), JSON.stringify({
      packetId: 'wp-demo', text: '实现搜索', submittedAt: '2026-08-14T00:00:00.000Z', commandId: 'cmd-demo', payload: { taskId: '00000000-0000-4000-8000-000000000001' }
    }), 'utf8')

    const source = await ProjectStateSource.create(project, {})
    try {
      const snapshot = await source.read()
      expect(snapshot.requirements).toEqual([expect.objectContaining({ packetId: 'wp-demo', taskId: '00000000-0000-4000-8000-000000000001' })])
    } finally {
      source.close()
    }
  })
})
