import { createHash } from 'node:crypto'
import { createReadStream } from 'node:fs'
import { lstat, realpath, stat } from 'node:fs/promises'
import { isAbsolute, relative, resolve, sep } from 'node:path'
import { MAX_PROJECT_FILE_REFERENCES, type ProjectFileReference } from '../shared/protocol'

export const MAX_PROJECT_FILE_BYTES = 20 * 1024 * 1024
export const MAX_PROJECT_FILE_TOTAL_BYTES = 50 * 1024 * 1024

export class ProjectFileReferenceError extends Error {
  readonly code: string

  constructor(code: string, message: string) {
    super(message)
    this.name = 'ProjectFileReferenceError'
    this.code = code
  }
}

export async function inspectProjectFileReferences(
  projectRoot: string,
  selectedPaths: readonly string[]
): Promise<readonly ProjectFileReference[]> {
  if (selectedPaths.length > MAX_PROJECT_FILE_REFERENCES) {
    throw new ProjectFileReferenceError(
      'TOO_MANY_FILES',
      `一次最多引用 ${MAX_PROJECT_FILE_REFERENCES} 个文件。`
    )
  }

  const canonicalRoot = await realpath(resolve(projectRoot))
  const references: ProjectFileReference[] = []
  const seen = new Set<string>()
  let totalBytes = 0

  for (const selectedPath of selectedPaths) {
    if (!isAbsolute(selectedPath)) {
      throw new ProjectFileReferenceError('FILE_PATH_NOT_ABSOLUTE', '选择的文件路径不是绝对路径。')
    }

    const selectedStat = await lstat(selectedPath)
    if (selectedStat.isSymbolicLink()) {
      throw new ProjectFileReferenceError('FILE_SYMLINK', '不能引用符号链接文件。')
    }

    const canonicalFile = await realpath(resolve(selectedPath))
    const projectRelativePath = relative(canonicalRoot, canonicalFile)
    if (!isWithinProject(projectRelativePath)) {
      throw new ProjectFileReferenceError('FILE_OUTSIDE_PROJECT', '只能引用当前项目目录内的文件。')
    }

    const relativePath = projectRelativePath.split(sep).join('/')
    const firstSegment = relativePath.split('/')[0]?.toLocaleLowerCase('en-US')
    if (firstSegment === '.codentum' || firstSegment === '.git') {
      throw new ProjectFileReferenceError('INTERNAL_FILE', '不能引用 .codentum 或 .git 内部文件。')
    }

    const identity = normalizeForIdentity(canonicalFile)
    if (seen.has(identity)) continue
    seen.add(identity)

    const before = await stat(canonicalFile)
    if (!before.isFile()) {
      throw new ProjectFileReferenceError('FILE_NOT_REGULAR', '只能引用普通文件。')
    }
    if (before.size > MAX_PROJECT_FILE_BYTES) {
      throw new ProjectFileReferenceError('FILE_TOO_LARGE', `单个文件不能超过 ${formatMiB(MAX_PROJECT_FILE_BYTES)}。`)
    }
    totalBytes += before.size
    if (totalBytes > MAX_PROJECT_FILE_TOTAL_BYTES) {
      throw new ProjectFileReferenceError('FILES_TOO_LARGE', `所选文件合计不能超过 ${formatMiB(MAX_PROJECT_FILE_TOTAL_BYTES)}。`)
    }

    const sha256 = await sha256File(canonicalFile)
    const after = await stat(canonicalFile)
    if (before.size !== after.size || before.mtimeMs !== after.mtimeMs) {
      throw new ProjectFileReferenceError('FILE_CHANGED', `读取期间文件发生变化：${relativePath}`)
    }

    references.push({ path: relativePath, sizeBytes: before.size, sha256 })
  }

  return references
}

function isWithinProject(projectRelativePath: string): boolean {
  return projectRelativePath !== '' &&
    projectRelativePath !== '..' &&
    !projectRelativePath.startsWith(`..${sep}`) &&
    !isAbsolute(projectRelativePath)
}

function normalizeForIdentity(path: string): string {
  return process.platform === 'win32' ? path.toLocaleLowerCase('en-US') : path
}

function sha256File(path: string): Promise<string> {
  return new Promise((resolveHash, rejectHash) => {
    const hash = createHash('sha256')
    const stream = createReadStream(path)
    stream.on('error', rejectHash)
    stream.on('data', (chunk) => hash.update(chunk))
    stream.on('end', () => resolveHash(hash.digest('hex')))
  })
}

function formatMiB(bytes: number): string {
  return `${Math.round(bytes / (1024 * 1024))} MiB`
}
