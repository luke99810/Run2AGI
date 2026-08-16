/**
 * 模型配置桥的判据 —— 界面配的东西必须真的到达引擎。
 *
 * ★ 这组守的是一段**此前完全不存在**的链路。在它之前：
 *   `safeStorage.encryptString` 有（写入），全仓库没有任何 decryptString（读出），
 *   引擎进程拿到的是 `...process.env`，也就是 Electron 从操作系统继承的那份。
 *   使用者在界面上填了 Key，界面显示「已配置」，引擎照旧读环境变量。
 *
 *   **一个会撒谎的已配置状态，比功能缺失难查得多** —— 后者看得见。
 */
import { describe, expect, it, afterEach } from 'vitest'
import { mkdtemp, readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { agentKeyEnvName, publishModelConfig, type ModelConfigSource } from './SidecarManager'
import { GLOBAL_SCOPE, ORCHESTRATOR_SCOPE } from '../../../shared/protocol'

const created: string[] = []
afterEach(async () => {
  await Promise.all(created.splice(0).map((path) => rm(path, { recursive: true, force: true })))
})

function source(
  endpoints: Record<string, { model?: string; effort?: string; baseUrl?: string; systemPrompt?: string }>,
  keysByRole: Record<string, string> = {},
  undecryptable: readonly string[] = []
): ModelConfigSource {
  return { endpoints: () => endpoints, resolveSecrets: () => ({ keysByRole, undecryptable }), cloudSkillsCatalog: () => '' }
}

async function tempProject(): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), 'codentum-modelcfg-'))
  created.push(root)
  return root
}

describe('模型配置桥', () => {
  it('把三级配置写成引擎读得懂的 agent-config.json', async () => {
    const root = await tempProject()
    await publishModelConfig(
      source({
        [GLOBAL_SCOPE]: { model: 'qwen-plus', effort: 'medium' },
        [ORCHESTRATOR_SCOPE]: { model: 'qwen3-max', effort: 'xhigh' },
        coder: { model: 'deepseek-v4-pro' }
      }),
      root
    )
    const written = JSON.parse(await readFile(join(root, '.codentum', 'agent-config.json'), 'utf8'))

    expect(written.schema).toBe('codentum.agent-config.v1')
    expect(written.global).toMatchObject({ model: 'qwen-plus' })
    expect(written.orchestrator).toMatchObject({ model: 'qwen3-max', effort: 'xhigh' })
    expect(written.agents.coder).toMatchObject({ model: 'deepseek-v4-pro' })
    // ★ 作用域不能混进 agents —— 混进去的话引擎会把 "__global__" 当成一个角色。
    expect(written.agents[GLOBAL_SCOPE]).toBeUndefined()
    expect(written.agents[ORCHESTRATOR_SCOPE]).toBeUndefined()
  })

  it('密钥走环境变量，绝不写进配置文件', async () => {
    const root = await tempProject()
    const env = await publishModelConfig(source({ coder: { model: 'm' } }, { coder: 'sk-super-secret' }), root)
    const raw = await readFile(join(root, '.codentum', 'agent-config.json'), 'utf8')

    // ★ 这是这次改动的安全底线：把密钥写进文件会让 safeStorage 那层加密
    //   变成装饰。文件里只许出现**变量名**。
    expect(raw).not.toContain('sk-super-secret')
    expect(env[agentKeyEnvName('coder')]).toBe('sk-super-secret')
    expect(JSON.parse(raw).agents.coder.apiKeyEnv).toBe(agentKeyEnvName('coder'))
  })

  it('全局那把 Key 用引擎本来就认的变量名', async () => {
    const root = await tempProject()
    const env = await publishModelConfig(source({}, { [GLOBAL_SCOPE]: 'sk-global' }), root)
    // ★ 造一个新变量名会让引擎的 _KEY_ENVS 那条既有约定失效 ——
    //   而那条约定是「命令行/环境变量启动」这条路径唯一的入口。
    expect(env['DASHSCOPE_API_KEY']).toBe('sk-global')
  })

  it('主 Agent 的 Key 落到 planner —— 界面叫法与代码角色的映射只此一处', async () => {
    const root = await tempProject()
    const env = await publishModelConfig(source({}, { [ORCHESTRATOR_SCOPE]: 'sk-main' }), root)
    expect(env[agentKeyEnvName('planner')]).toBe('sk-main')
  })

  it('只配了 Key、没配模型的 Agent 也要进表', async () => {
    // ★ 不进表的话引擎不知道该用哪个环境变量去取它的凭据 ——
    //   那把 Key 就白配了，而界面上它显示「已配置」。
    const root = await tempProject()
    const env = await publishModelConfig(source({}, { qa: 'sk-qa' }), root)
    const written = JSON.parse(await readFile(join(root, '.codentum', 'agent-config.json'), 'utf8'))

    expect(env[agentKeyEnvName('qa')]).toBe('sk-qa')
    expect(written.agents.qa).toEqual({ apiKeyEnv: agentKeyEnvName('qa') })
  })

  it('系统提示词随配置一起送到引擎 —— 此前它从没离开过桌面端', async () => {
    // ★ 在这条之前，systemPrompt 存在本地、界面显示已保存，
    //   而引擎从不读它 —— 与那把从没被解密过的 API Key 是同一种缺陷。
    const root = await tempProject()
    await publishModelConfig(
      source({
        [GLOBAL_SCOPE]: { systemPrompt: '所有代码写中文注释。' },
        coder: { model: 'm', systemPrompt: '优先用组合。' }
      }),
      root
    )
    const written = JSON.parse(await readFile(join(root, '.codentum', 'agent-config.json'), 'utf8'))

    expect(written.global.systemPrompt).toBe('所有代码写中文注释。')
    expect(written.agents.coder.systemPrompt).toBe('优先用组合。')
  })

  it('环境变量名的约定与引擎侧完全一致', () => {
    // ★ 引擎侧是 `codentum_engine.model_config.agent_key_env`。
    //   两边各写一份字符串拼接，改了一边不会有任何东西报错 ——
    //   现象是「配了 Key 但那个 Agent 用不上」，而两边看起来都对。
    expect(agentKeyEnvName('coder')).toBe('CODENTUM_AGENT_KEY__CODER')
    expect(agentKeyEnvName('planner')).toBe('CODENTUM_AGENT_KEY__PLANNER')
  })
})
