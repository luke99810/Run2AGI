import { afterEach, describe, expect, it } from 'vitest'
import { mkdir, mkdtemp, readFile, rm, stat, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { gunzipSync } from 'node:zlib'
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
    if (process.platform !== 'win32') expect((await stat(archive)).mode & 0o777).toBe(0o644)
  })

  it('excludes build caches — otherwise this very repository cannot be packaged', async () => {
    // ★ 这条测的是一次**实测出来的**失败，不是假想的：
    //   拿本仓库自己去打包会抛「交付包路径过长」——
    //   96 个超限路径全部来自 .mypy_cache 与 __pycache__，
    //   而真实源码/文档超限的是 0 个。挡住交付的不是源码，
    //   是本来就不该进包的东西。
    const root = await mkdtemp(join(tmpdir(), 'codentum-artifact-cache-'))
    const output = await mkdtemp(join(tmpdir(), 'codentum-artifact-output-'))
    created.push(root, output)

    // 一条真实存在于本仓库的 .mypy_cache 路径（119 字节 > tar 的 100 上限）
    const deep = join('.mypy_cache', '3.11', 'langsmith', '_openapi_client', 'types')
    await mkdir(join(root, deep), { recursive: true })
    await writeFile(
      join(root, deep, 'annotation_queue_retrieve_annotation_queues_response.meta.json'),
      '{}',
      'utf8'
    )
    await mkdir(join(root, '__pycache__'), { recursive: true })
    await writeFile(join(root, '__pycache__', 'stale.cpython-311.pyc'), 'bytecode', 'utf8')
    await mkdir(join(root, '.venv', 'Lib'), { recursive: true })
    await writeFile(join(root, '.venv', 'Lib', 'pyvenv.cfg'), 'home = C:/Anaconda\n', 'utf8')
    await writeFile(join(root, 'main.py'), 'print(1)\n', 'utf8')

    const archive = join(output, 'demo.tar.gz')
    // ★ 断言「不抛」本身就是判据 —— 排除表少一项，这里就是那句「路径过长」。
    const result = await packageProjectArtifact(root, archive, 'packet-cache')

    expect(result.fileCount).toBe(1)
    expect(result.verified).toBe(true)
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

  it('marks the cold-start entrypoint executable on every packaging host', async () => {
    const root = await mkdtemp(join(tmpdir(), 'codentum-artifact-start-'))
    const output = await mkdtemp(join(tmpdir(), 'codentum-artifact-output-'))
    created.push(root, output)
    await writeFile(join(root, 'codentum-start.sh'), '#!/bin/sh\nexit 0\n', 'utf8')
    const archive = join(output, 'demo.tar.gz')

    await packageProjectArtifact(root, archive)

    const tar = gunzipSync(await readFile(archive))
    let offset = 0
    let startupMode: number | undefined
    while (offset + 512 <= tar.length) {
      const header = tar.subarray(offset, offset + 512)
      if (header.every((value) => value === 0)) break
      const path = header.subarray(0, 100).toString('utf8').replace(/\0.*$/u, '')
      const size = Number.parseInt(header.subarray(124, 136).toString('ascii').replace(/\0.*$/u, '').trim() || '0', 8)
      if (path === 'project/codentum-start.sh') {
        startupMode = Number.parseInt(header.subarray(100, 108).toString('ascii').replace(/\0.*$/u, '').trim(), 8)
        break
      }
      offset += 512 + Math.ceil(size / 512) * 512
    }
    expect(startupMode).toBe(0o755)
  })
})
