import { createHash } from 'node:crypto'
import { execFile } from 'node:child_process'
import type { Stats } from 'node:fs'
import { lstat, realpath, stat } from 'node:fs/promises'
import { basename, isAbsolute, join, relative, resolve, sep } from 'node:path'
import type { SnapshotSourceDescriptor } from '../shared/protocol'
import { DirectoryStateSource, type DirectoryStateSourceOptions } from './directory-state-source'

export class ProjectPathError extends Error {
  readonly code: string

  constructor(code: string, message: string) {
    super(message)
    this.name = 'ProjectPathError'
    this.code = code
  }
}

export class ProjectStateSource extends DirectoryStateSource {
  readonly projectRoot: string

  private constructor(
    projectRoot: string,
    descriptor: SnapshotSourceDescriptor,
    options: DirectoryStateSourceOptions
  ) {
    super(descriptor, join(projectRoot, '.codentum'), {
      ...options,
      additionalWorkerStateDirectories: () => linkedWorkerStateDirectories(projectRoot)
    })
    this.projectRoot = projectRoot
  }

  static async create(
    rootPath: string,
    options: DirectoryStateSourceOptions = {}
  ): Promise<ProjectStateSource> {
    const projectRoot = await validateProjectRoot(rootPath)
    const id = createHash('sha256').update(normalizeForIdentity(projectRoot)).digest('hex').slice(0, 24)
    const descriptor: SnapshotSourceDescriptor = {
      id: `project:${id}`,
      kind: 'project',
      label: basename(projectRoot),
      rootPath: projectRoot
    }
    return new ProjectStateSource(projectRoot, descriptor, options)
  }
}

async function linkedWorkerStateDirectories(projectRoot: string): Promise<readonly string[]> {
  const worktrees = await gitWorktreePaths(projectRoot)
  const selectedIdentity = normalizeForIdentity(projectRoot)
  const stateDirectories: string[] = []
  for (const worktree of worktrees) {
    try {
      const canonicalRoot = await realpath(resolve(worktree))
      if (normalizeForIdentity(canonicalRoot) === selectedIdentity) continue
      const stateDirectory = join(canonicalRoot, '.codentum')
      const stateStat = await lstat(stateDirectory)
      if (stateStat.isSymbolicLink() || !stateStat.isDirectory()) continue
      const canonicalState = await realpath(stateDirectory)
      if (isWithin(canonicalRoot, canonicalState)) stateDirectories.push(canonicalState)
    } catch {
      // A linked worktree can disappear between Git enumeration and state reading.
    }
  }
  return stateDirectories
}

function gitWorktreePaths(projectRoot: string): Promise<readonly string[]> {
  return new Promise((resolvePaths) => {
    execFile(
      'git',
      ['-C', projectRoot, 'worktree', 'list', '--porcelain', '-z'],
      { encoding: 'utf8', maxBuffer: 1024 * 1024, timeout: 5_000, windowsHide: true },
      (error, stdout) => {
        if (error !== null) {
          resolvePaths([])
          return
        }
        resolvePaths(stdout
          .split('\0')
          .filter((record) => record.startsWith('worktree '))
          .map((record) => record.slice('worktree '.length)))
      }
    )
  })
}

export async function validateProjectRoot(rootPath: string): Promise<string> {
  if (rootPath.trim().length === 0 || !isAbsolute(rootPath)) {
    throw new ProjectPathError('PROJECT_PATH_NOT_ABSOLUTE', 'Project path must be an absolute path.')
  }

  let projectRoot: string
  try {
    projectRoot = await realpath(resolve(rootPath))
  } catch {
    throw new ProjectPathError('PROJECT_PATH_NOT_FOUND', `Project path does not exist: ${rootPath}`)
  }

  const projectStat = await stat(projectRoot)
  if (!projectStat.isDirectory()) {
    throw new ProjectPathError('PROJECT_PATH_NOT_DIRECTORY', `Project path is not a directory: ${projectRoot}`)
  }

  const statePath = join(projectRoot, '.codentum')
  let stateStat: Stats | undefined
  try {
    stateStat = await lstat(statePath)
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return projectRoot
    throw new ProjectPathError('STATE_DIRECTORY_UNREADABLE', `Could not inspect .codentum in project: ${projectRoot}`)
  }

  if (stateStat.isSymbolicLink()) {
    throw new ProjectPathError('STATE_DIRECTORY_SYMLINK', '.codentum must not be a symbolic link.')
  }
  if (!stateStat.isDirectory()) {
    throw new ProjectPathError('STATE_DIRECTORY_NOT_DIRECTORY', '.codentum is not a directory.')
  }

  const canonicalStatePath = await realpath(statePath)
  if (!isWithin(projectRoot, canonicalStatePath)) {
    throw new ProjectPathError('STATE_DIRECTORY_OUTSIDE_PROJECT', '.codentum resolves outside the selected project.')
  }
  return projectRoot
}

function normalizeForIdentity(path: string): string {
  return process.platform === 'win32' ? path.toLocaleLowerCase('en-US') : path
}

function isWithin(parent: string, candidate: string): boolean {
  const rel = relative(parent, candidate)
  return rel === '' || (rel !== '..' && !rel.startsWith(`..${sep}`) && !isAbsolute(rel))
}
