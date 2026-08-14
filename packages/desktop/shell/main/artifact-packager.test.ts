import { afterEach, describe, expect, it } from 'vitest'
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { packageProjectArtifact, verifyArtifactArchive } from './artifact-packager'

describe('project artifact packager', () => {
  const created: string[] = []
  afterEach(async () => Promise.all(created.splice(0).map((path) => rm(path, { recursive: true, force: true }))))

  it('creates a verified source archive without runtime state, dependencies, or secrets', async () => {
    const root = await mkdtemp(join(tmpdir(), 'codentum-artifact-source-'))
    const output = await mkdtemp(join(tmpdir(), 'codentum-artifact-output-'))
    created.push(root, output)
    await mkdir(join(root, 'src'), { recursive: true })
    await mkdir(join(root, '.codentum'), { recursive: true })
    await mkdir(join(root, 'node_modules', 'example'), { recursive: true })
    await writeFile(join(root, 'src', 'main.ts'), 'export const value = 1\n', 'utf8')
    await writeFile(join(root, 'README.md'), '# Demo\n', 'utf8')
    await writeFile(join(root, '.env'), 'SECRET=do-not-package\n', 'utf8')
    await writeFile(join(root, '.codentum', 'state.json'), '{}', 'utf8')
    await writeFile(join(root, 'node_modules', 'example', 'index.js'), 'ignored', 'utf8')
    const archive = join(output, 'demo.tar.gz')

    const result = await packageProjectArtifact(root, archive, 'packet-1', new Date('2026-08-14T00:00:00.000Z'))

    expect(result).toMatchObject({ fileName: 'demo.tar.gz', packetId: 'packet-1', verified: true, fileCount: 2 })
    expect(result.sha256).toMatch(/^[0-9a-f]{64}$/u)
    expect(result.log.at(-1)).toContain('隔离解包验证通过')
    await expect(readFile(archive)).resolves.toHaveLength(result.archiveBytes)
    await expect(verifyArtifactArchive(archive)).resolves.toBeUndefined()
  })

  it('rejects a modified archive during isolated verification', async () => {
    const root = await mkdtemp(join(tmpdir(), 'codentum-artifact-source-'))
    const output = await mkdtemp(join(tmpdir(), 'codentum-artifact-output-'))
    created.push(root, output)
    await writeFile(join(root, 'main.txt'), 'hello', 'utf8')
    const archive = join(output, 'demo.tar.gz')
    await packageProjectArtifact(root, archive)
    const content = await readFile(archive)
    const index = Math.max(0, content.length - 8)
    content[index] = (content[index] ?? 0) ^ 0xff
    await writeFile(archive, content)
    await expect(verifyArtifactArchive(archive)).rejects.toThrow()
  })
})
