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
})
