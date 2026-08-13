import { mkdtemp, readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('electron', () => ({
  safeStorage: {
    isEncryptionAvailable: () => true,
    encryptString: (value: string) => Buffer.from(`encrypted:${value}`, 'utf8')
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
