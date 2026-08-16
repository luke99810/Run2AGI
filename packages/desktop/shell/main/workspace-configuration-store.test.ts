import { mkdtemp, readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('electron', () => ({
  safeStorage: {
    isEncryptionAvailable: () => true,
    encryptString: (value: string) => Buffer.from(`encrypted:${value}`, 'utf8'),
    // ★ 这个 mock 此前**没有 decryptString** —— 因为生产代码里根本没人读。
    //   加它本身就是这次修复的一个标记：密钥终于有了读出口。
    decryptString: (value: Buffer) => {
      const text = value.toString('utf8')
      if (!text.startsWith('encrypted:')) throw new Error('解不开')
      return text.slice('encrypted:'.length)
    }
  }
}))

import { WorkspaceConfigurationStore } from './workspace-configuration-store'

const roots: string[] = []
async function root(): Promise<string> { const value = await mkdtemp(join(tmpdir(), 'codentum-config-')); roots.push(value); return value }
afterEach(async () => { await Promise.all(roots.splice(0).map((path) => rm(path, { recursive: true, force: true }))) })

describe('WorkspaceConfigurationStore', () => {
  it('persists generic connector configuration without returning or storing plaintext credentials', async () => {
    const directory = await root()
    const store = new WorkspaceConfigurationStore(directory)
    await store.initialize()
    const saved = await store.saveConnector({ provider: 'custom', name: '研发协作账号', accountLabel: 'codentum', enabled: true, credential: 'secret-token' })
    expect(saved).toMatchObject({ provider: 'custom', credentialConfigured: true })
    expect(saved).not.toHaveProperty('encryptedCredential')
    expect(await readFile(join(directory, 'configuration.json'), 'utf8')).not.toContain('secret-token')
    const reopened = new WorkspaceConfigurationStore(directory)
    await reopened.initialize()
    expect(reopened.listConnectors()).toEqual([expect.objectContaining({ id: saved.id, credentialConfigured: true })])
    expect(await reopened.removeConnector(saved.id)).toBe(true)
    expect(reopened.listConnectors()).toEqual([])
  })

  it('keeps every Agent configuration isolated and exposes only API Key presence', async () => {
    const directory = await root()
    const store = new WorkspaceConfigurationStore(directory)
    await store.initialize()
    await store.saveAgent('coder', { systemPrompt: 'Implement only assigned packets.', apiKey: 'coder-key' })
    await store.saveAgent('reviewer', { systemPrompt: 'Review independently.' })
    await store.setAgentDocument('coder', join(directory, 'coder-system.md'))
    expect(store.listAgents()).toEqual(expect.arrayContaining([
      expect.objectContaining({ roleId: 'coder', systemDocumentName: 'coder-system.md', apiKeyConfigured: true }),
      expect.objectContaining({ roleId: 'reviewer', apiKeyConfigured: false })
    ]))
    expect(JSON.stringify(store.listAgents())).not.toContain('coder-key')
  })

  it('creates and deletes local custom Agent configuration without allowing system role deletion', async () => {
    const directory = await root()
    const store = new WorkspaceConfigurationStore(directory)
    await store.initialize()
    const id = 'custom:11111111-1111-4111-8111-111111111111'
    const saved = await store.saveAgent(id, { name: '前端开发 Agent', systemPrompt: '只修改授权的前端文件。', apiKey: 'agent-key' })
    expect(saved).toMatchObject({ roleId: id, name: '前端开发 Agent', custom: true, apiKeyConfigured: true })
    expect(JSON.stringify(store.listAgents())).not.toContain('agent-key')
    await expect(store.removeAgent('coder')).rejects.toThrow('系统岗位配置不能在 C 侧删除')
    expect(await store.removeAgent(id)).toBe(true)
    expect(store.listAgents()).toEqual([])
  })

  it('persists MCP Server configuration separately from application plugins', async () => {
    const directory = await root()
    const store = new WorkspaceConfigurationStore(directory)
    await store.initialize()
    const mcp = await store.saveMcp({ name: 'Browser tools', transport: 'stdio', endpoint: 'npx browser-mcp', enabled: true })
    expect(store.listMcp()).toEqual([expect.objectContaining({ id: mcp.id, transport: 'stdio' })])
    expect(store.listConnectors()).toEqual([])
  })
})


describe('模型接入配置（三级覆盖）', () => {
  it('保存并读回某个 Agent 的模型与强度', async () => {
    const store = new WorkspaceConfigurationStore(await root())
    await store.initialize()
    await store.saveAgent('coder', { endpoint: { model: 'deepseek-v4-pro', effort: 'max' } })

    expect(store.endpoints()['coder']).toEqual({ model: 'deepseek-v4-pro', effort: 'max' })
  })

  it('空串 = 取消这一层，字段不传 = 不改动', async () => {
    // ★ 这条守的是一个差点被类型检查「修」掉的语义：
    //   为了迁就 exactOptionalPropertyTypes，空 effort 一度被写成
    //   「省略该字段」—— tsc 绿了，而清除变成了静默无操作。
    const store = new WorkspaceConfigurationStore(await root())
    await store.initialize()
    await store.saveAgent('coder', { endpoint: { model: 'm1', effort: 'high', baseUrl: 'u1' } })

    // 只动 effort，其余不传 → 其余保持
    await store.saveAgent('coder', { endpoint: { effort: '' } })
    expect(store.endpoints()['coder']).toEqual({ model: 'm1', baseUrl: 'u1' })

    // 全部清空 → 整层消失
    await store.saveAgent('coder', { endpoint: { model: '', baseUrl: '' } })
    expect(store.endpoints()['coder']).toBeUndefined()
  })

  it('两个作用域（全局 / 主 Agent）也能保存', async () => {
    const store = new WorkspaceConfigurationStore(await root())
    await store.initialize()
    await store.saveAgent('__global__', { endpoint: { model: 'qwen-plus' } })
    await store.saveAgent('__orchestrator__', { endpoint: { model: 'qwen3-max' } })

    expect(store.endpoints()['__global__']).toEqual({ model: 'qwen-plus' })
    expect(store.endpoints()['__orchestrator__']).toEqual({ model: 'qwen3-max' })
  })

  it('密钥能被解回来 —— 此前它只进不出', async () => {
    // ★ 在这条之前，safeStorage.encryptString 有，而全仓库没有任何
    //   decryptString。存进去的 Key 从来没有被读出来过，
    //   而界面显示「已配置」。**一个会撒谎的已配置状态。**
    const store = new WorkspaceConfigurationStore(await root())
    await store.initialize()
    await store.saveAgent('coder', { apiKey: 'sk-real-key' })

    expect(store.resolveSecrets().keysByRole['coder']).toBe('sk-real-key')
  })

  it('解不开的旧密文被单列出来，而不是当作没配', async () => {
    // ★ 换机器或重装系统后旧密文解不开是正常的。静默跳过会让使用者
    //   盯着一个显示「已配置」但永远不生效的状态等下去。
    const directory = await root()
    const store = new WorkspaceConfigurationStore(directory)
    await store.initialize()
    await store.saveAgent('coder', { apiKey: 'sk-ok' })
    await writeCorruptedKey(directory, 'qa')

    const reloaded = new WorkspaceConfigurationStore(directory)
    await reloaded.initialize()
    const { keysByRole, undecryptable } = reloaded.resolveSecrets()

    expect(keysByRole['coder']).toBe('sk-ok')
    expect(undecryptable).toContain('qa')
  })
})

async function writeCorruptedKey(directory: string, roleId: string): Promise<void> {
  const path = join(directory, 'configuration.json')
  const file = JSON.parse(await readFile(path, 'utf8'))
  file.agents.push({
    roleId,
    systemPrompt: '',
    encryptedApiKey: Buffer.from('这不是本机加密的', 'utf8').toString('base64'),
    updatedAt: new Date().toISOString()
  })
  const { writeFile } = await import('node:fs/promises')
  await writeFile(path, JSON.stringify(file, null, 2), 'utf8')
}

describe('系统提示词的送出', () => {
  it('提示词出现在 endpoints() 里 —— 它第一次离开桌面端', async () => {
    const store = new WorkspaceConfigurationStore(await root())
    await store.initialize()
    await store.saveAgent('coder', { systemPrompt: '优先用组合。' })

    expect(store.endpoints()['coder']).toEqual({ systemPrompt: '优先用组合。' })
  })

  it('空白提示词不进表 —— 清空 = 不追加，不是追加一段空白', async () => {
    // ★ 追加空白会在提示词里留下一个没有内容的小节，
    //   模型看到一个空指令，而使用者以为自己已经删掉了。
    const store = new WorkspaceConfigurationStore(await root())
    await store.initialize()
    await store.saveAgent('coder', { systemPrompt: '   \n  ' })

    expect(store.endpoints()['coder']).toBeUndefined()
  })
})
