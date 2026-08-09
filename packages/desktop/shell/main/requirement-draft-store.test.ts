import { mkdir, mkdtemp, readFile, rm, stat, symlink, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import type { OperatorCommand } from '../../shared/protocol'
import { RequirementDraftStore } from './requirement-draft-store'

const temporaryDirectories: string[] = []

afterEach(async () => {
  await Promise.all(temporaryDirectories.splice(0).map((path) => rm(path, { force: true, recursive: true })))
})

async function temporaryDirectory(prefix: string): Promise<string> {
  const path = await mkdtemp(join(tmpdir(), prefix))
  temporaryDirectories.push(path)
  return path
}

function requirementCommand(scopeId: string, attachments: readonly unknown[]): OperatorCommand {
  return {
    commandId: 'command-attachment-test',
    runId: 'run-attachment-test',
    expectedRevision: 0,
    target: { agentId: 'intake' },
    action: 'submit_requirement',
    payload: {
      requirement: 'Read the attached binary fixture.',
      draftScope: scopeId,
      attachments
    },
    requestedAt: '2026-08-08T00:00:00.000Z'
  }
}

describe('RequirementDraftStore', () => {
  it('copies arbitrary files outside the project and persists the draft', async () => {
    const sourceRoot = await temporaryDirectory('codentum-source-')
    const storeRoot = await temporaryDirectory('codentum-drafts-')
    const sourcePath = join(sourceRoot, 'opaque.payload')
    const content = Buffer.from([0, 255, 17, 42, 99, 0, 8])
    await writeFile(sourcePath, content)

    const scopeId = 'project:0123456789abcdef01234567'
    const store = new RequirementDraftStore(storeRoot)
    await store.initialize()
    await store.save(scopeId, { text: 'A persisted requirement', attachments: [] })
    const added = await store.addFiles(scopeId, [sourcePath])

    expect(added.text).toBe('A persisted requirement')
    expect(added.attachments).toHaveLength(1)
    expect(added.attachments[0]?.name).toBe('opaque.payload')
    expect(added.attachments[0]?.kind).toBe('file')
    expect(added.attachments[0]?.fileCount).toBe(1)
    await rm(sourcePath)

    const reopened = new RequirementDraftStore(storeRoot)
    await reopened.initialize()
    expect(await reopened.load(scopeId)).toEqual(added)

    const prepared = await reopened.prepareRequirementCommand(
      requirementCommand(scopeId, added.attachments),
      sourceRoot
    )
    const engineAttachments = prepared.payload['attachments']
    expect(Array.isArray(engineAttachments)).toBe(true)
    const engineAttachment = Array.isArray(engineAttachments) ? engineAttachments[0] : undefined
    expect(engineAttachment).toMatchObject(added.attachments[0] ?? {})
    expect(engineAttachment).toHaveProperty('localPath')
    const localPath = typeof engineAttachment === 'object' && engineAttachment !== null
      ? (engineAttachment as Record<string, unknown>)['localPath']
      : undefined
    expect(typeof localPath).toBe('string')
    expect(localPath).not.toBe(sourcePath)
    expect(String(localPath)).toMatch(/opaque\.payload$/u)
    expect(await readFile(String(localPath))).toEqual(content)
  })

  it('removes the private copy after the last draft reference is discarded', async () => {
    const sourceRoot = await temporaryDirectory('codentum-source-')
    const storeRoot = await temporaryDirectory('codentum-drafts-')
    const sourcePath = join(sourceRoot, 'notes.any-extension')
    await writeFile(sourcePath, 'agent-readable attachment', 'utf8')

    const scopeId = 'project:fedcba9876543210fedcba98'
    const store = new RequirementDraftStore(storeRoot)
    await store.initialize()
    const added = await store.addFiles(scopeId, [sourcePath])
    const prepared = await store.prepareRequirementCommand(requirementCommand(scopeId, added.attachments), sourceRoot)
    const payload = prepared.payload['attachments']
    const first = Array.isArray(payload) ? payload[0] : undefined
    const localPath = typeof first === 'object' && first !== null
      ? (first as Record<string, unknown>)['localPath']
      : undefined
    expect(typeof localPath).toBe('string')

    const attachment = added.attachments[0]
    expect(attachment).toBeDefined()
    await store.discard(scopeId, attachment?.id ?? '')
    await expect(stat(String(localPath))).rejects.toMatchObject({ code: 'ENOENT' })
  })

  it('copies a folder recursively and gives the engine a readable private directory', async () => {
    const sourceRoot = await temporaryDirectory('codentum-folder-source-')
    const storeRoot = await temporaryDirectory('codentum-drafts-')
    const selectedFolder = join(sourceRoot, 'design-materials')
    await mkdir(join(selectedFolder, 'nested'), { recursive: true })
    await mkdir(join(selectedFolder, 'empty'))
    await writeFile(join(selectedFolder, 'brief.md'), '# Product brief\n', 'utf8')
    await writeFile(join(selectedFolder, 'nested', 'wireframe.binary'), Buffer.from([5, 4, 3, 2, 1]))

    const scopeId = 'project:00112233445566778899aabb'
    const store = new RequirementDraftStore(storeRoot)
    await store.initialize()
    const added = await store.addFolders(scopeId, [selectedFolder])
    const attachment = added.attachments[0]
    expect(attachment).toMatchObject({
      name: 'design-materials',
      kind: 'folder',
      fileCount: 2,
      sizeBytes: Buffer.byteLength('# Product brief\n') + 5
    })

    await rm(selectedFolder, { recursive: true })
    const reopened = new RequirementDraftStore(storeRoot)
    await reopened.initialize()
    const prepared = await reopened.prepareRequirementCommand(requirementCommand(scopeId, added.attachments), sourceRoot)
    const payload = prepared.payload['attachments']
    const first = Array.isArray(payload) ? payload[0] : undefined
    const localPath = typeof first === 'object' && first !== null
      ? (first as Record<string, unknown>)['localPath']
      : undefined
    expect(typeof localPath).toBe('string')
    expect(await readFile(join(String(localPath), 'brief.md'), 'utf8')).toBe('# Product brief\n')
    expect(await readFile(join(String(localPath), 'nested', 'wireframe.binary'))).toEqual(Buffer.from([5, 4, 3, 2, 1]))
    expect((await stat(join(String(localPath), 'empty'))).isDirectory()).toBe(true)
  })

  it('rejects links inside a selected folder instead of copying outside the tree', async () => {
    const sourceRoot = await temporaryDirectory('codentum-folder-source-')
    const storeRoot = await temporaryDirectory('codentum-drafts-')
    const selectedFolder = join(sourceRoot, 'selected')
    const outsideFolder = join(sourceRoot, 'outside')
    await mkdir(selectedFolder)
    await mkdir(outsideFolder)
    await writeFile(join(outsideFolder, 'private.txt'), 'must not be traversed', 'utf8')
    await symlink(outsideFolder, join(selectedFolder, 'linked-outside'), process.platform === 'win32' ? 'junction' : 'dir')

    const store = new RequirementDraftStore(storeRoot)
    await store.initialize()
    await expect(store.addFolders('project:aabbccddeeff001122334455', [selectedFolder]))
      .rejects.toMatchObject({ code: 'FOLDER_SYMLINK' })
    expect((await store.load('project:aabbccddeeff001122334455')).attachments).toHaveLength(0)
  })
})
