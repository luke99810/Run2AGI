import { createHash } from 'node:crypto'
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import {
  inspectProjectFileReferences,
  ProjectFileReferenceError
} from './index'
import { MAX_PROJECT_FILE_REFERENCES } from '../shared/protocol'

const temporaryDirectories: string[] = []

afterEach(async () => {
  await Promise.all(temporaryDirectories.splice(0).map((path) => rm(path, { recursive: true, force: true })))
})

describe('inspectProjectFileReferences', () => {
  it('returns only project-relative metadata for a verified file', async () => {
    const project = await temporaryDirectory('codentum-file-project-')
    const docs = join(project, 'docs')
    const path = join(docs, 'brief.md')
    await mkdir(docs)
    await writeFile(path, 'hello', 'utf8')

    await expect(inspectProjectFileReferences(project, [path])).resolves.toEqual([{
      path: 'docs/brief.md',
      sizeBytes: 5,
      sha256: createHash('sha256').update('hello').digest('hex')
    }])
  })

  it('rejects files outside the selected project', async () => {
    const project = await temporaryDirectory('codentum-file-project-')
    const outside = await temporaryDirectory('codentum-file-outside-')
    const path = join(outside, 'brief.md')
    await writeFile(path, 'outside', 'utf8')

    await expect(inspectProjectFileReferences(project, [path])).rejects.toMatchObject({
      code: 'FILE_OUTSIDE_PROJECT'
    })
  })

  it('rejects Codentum state and Git internals', async () => {
    const project = await temporaryDirectory('codentum-file-project-')
    const stateDirectory = join(project, '.codentum')
    const path = join(stateDirectory, 'budget.json')
    await mkdir(stateDirectory)
    await writeFile(path, '{}', 'utf8')

    await expect(inspectProjectFileReferences(project, [path])).rejects.toMatchObject({
      code: 'INTERNAL_FILE'
    })
  })

  it('rejects an oversized selection before reading any file', async () => {
    const project = await temporaryDirectory('codentum-file-project-')
    const paths = Array.from(
      { length: MAX_PROJECT_FILE_REFERENCES + 1 },
      (_, index) => resolve(project, `missing-${index}.txt`)
    )

    await expect(inspectProjectFileReferences(project, paths)).rejects.toBeInstanceOf(ProjectFileReferenceError)
    await expect(inspectProjectFileReferences(project, paths)).rejects.toMatchObject({ code: 'TOO_MANY_FILES' })
  })
})

async function temporaryDirectory(prefix: string): Promise<string> {
  const directory = await mkdtemp(join(tmpdir(), prefix))
  temporaryDirectories.push(directory)
  return directory
}
