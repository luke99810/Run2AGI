import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import type { OperatorCommand } from '../../shared/protocol'
import { ManagedResourceStore } from './managed-resource-store'

const temporaryRoots: string[] = []

async function temporaryRoot(): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), 'codentum-resources-'))
  temporaryRoots.push(root)
  return root
}

function command(skillIds: readonly string[]): OperatorCommand {
  return {
    commandId: 'command-managed-resource-1',
    runId: 'run-1',
    expectedRevision: 0,
    target: { agentId: 'operator' },
    action: 'submit_requirement',
    payload: { requirement: '实现功能', skillIds },
    requestedAt: '2026-08-12T00:00:00.000Z'
  }
}

afterEach(async () => {
  await Promise.all(temporaryRoots.splice(0).map((root) => rm(root, { recursive: true, force: true })))
})

describe('ManagedResourceStore', () => {
  it('reads SKILL.md metadata without exposing the local path to the renderer', async () => {
    const root = await temporaryRoot()
    const skill = join(root, 'skills', 'ui-audit')
    await mkdir(skill, { recursive: true })
    await writeFile(join(skill, 'SKILL.md'), '---\nname: UI Audit\ndescription: Verify desktop interaction quality.\n---\n', 'utf8')
    const store = new ManagedResourceStore(join(root, 'registry'))
    await store.initialize()

    const [added] = await store.addLocal('skill', [skill], 'folder')

    expect(added).toMatchObject({ name: 'UI Audit', description: 'Verify desktop interaction quality.', scope: 'role', roleId: 'coder' })
    expect(added).not.toHaveProperty('localPath')
    expect(JSON.stringify(await store.list('skill'))).not.toContain(skill)
  })

  it('emits the stable A/B resource selection payload only for selected enabled resources', async () => {
    const root = await temporaryRoot()
    const file = join(root, 'knowledge.md')
    await writeFile(file, '# Knowledge\n', 'utf8')
    const store = new ManagedResourceStore(join(root, 'registry'))
    await store.initialize()
    const [resource] = await store.addLocal('knowledge', [file], 'file')
    if (resource === undefined) throw new Error('Expected resource')

    const prepared = await store.prepareCommand({ ...command([]), payload: { ...command([]).payload, knowledgeIds: [resource.id] } })

    expect(prepared.payload['resourceSelectionContract']).toBe('codentum.resource-selection.v1')
    expect(prepared.payload['resourceSelections']).toEqual([expect.objectContaining({
      id: resource.id,
      kind: 'knowledge',
      scope: 'project',
      sourceKind: 'file',
      localPath: file
    })])

    await store.update(resource.id, { enabled: false })
    const disabled = await store.prepareCommand({ ...command([]), payload: { ...command([]).payload, knowledgeIds: [resource.id] } })
    expect(disabled.payload['resourceSelections']).toEqual([])
  })

  it('persists scope configuration, rejects duplicate and unsafe Git sources, and removes entries', async () => {
    const root = await temporaryRoot()
    const file = join(root, 'plugin.json')
    await writeFile(file, '{}', 'utf8')
    const registry = join(root, 'registry')
    const store = new ManagedResourceStore(registry)
    await store.initialize()
    const [resource] = await store.addLocal('plugin', [file], 'file')
    if (resource === undefined) throw new Error('Expected resource')
    await expect(store.addLocal('plugin', [file], 'file')).rejects.toThrow('已经登记')
    await expect(store.addGit('skill', 'http://example.com/skill.git')).rejects.toThrow('HTTPS')
    await expect(store.addGit('skill', 'https://user:secret@example.com/skill.git')).rejects.toThrow('HTTPS')
    await expect(store.addGit('skill', 'https://example.com/skill.git?token=secret')).rejects.toThrow('查询参数')
    await store.update(resource.id, { scope: 'global' })

    const reopened = new ManagedResourceStore(registry)
    await reopened.initialize()
    expect(await reopened.list('plugin')).toEqual([expect.objectContaining({ id: resource.id, scope: 'global' })])
    expect(await reopened.remove(resource.id)).toBe(true)
    expect(await reopened.list('plugin')).toEqual([])
    const registryText = await readFile(join(registry, 'registry.json'), 'utf8')
    expect(registryText).not.toContain('plugin.json')
  })

  it('refuses to submit a selected local source that was moved or deleted', async () => {
    const root = await temporaryRoot()
    const file = join(root, 'context.txt')
    await writeFile(file, 'context', 'utf8')
    const store = new ManagedResourceStore(join(root, 'registry'))
    await store.initialize()
    const [resource] = await store.addLocal('knowledge', [file], 'file')
    if (resource === undefined) throw new Error('Expected resource')
    await rm(file)

    await expect(store.prepareCommand({ ...command([]), payload: { ...command([]).payload, knowledgeIds: [resource.id] } })).rejects.toThrow('原始位置已失效')
    expect(await store.list('knowledge')).toEqual([expect.objectContaining({ runtimeStatus: 'missing_source' })])
  })
})
