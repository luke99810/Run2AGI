import { existsSync, readdirSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { SnapshotSourceDescriptor, StateSnapshot } from '../shared/protocol'
import type { DirectoryStateSourceOptions } from './directory-state-source'
import { FixtureStateSource } from './fixture-state-source'
import { ProjectStateSource } from './project-state-source'
import type { StateListener, StateSource } from './state-source'

export interface StateHubOptions extends DirectoryStateSourceOptions {
  readonly fixtureRoot?: string
}

export class StateHub {
  private readonly sources = new Map<string, StateSource>()
  private readonly sourceOptions: DirectoryStateSourceOptions
  private selectedProjectId: string | undefined
  private closed = false

  constructor(options: StateHubOptions = {}) {
    this.sourceOptions = {
      ...(options.pollIntervalMs === undefined ? {} : { pollIntervalMs: options.pollIntervalMs }),
      ...(options.staleAfterMs === undefined ? {} : { staleAfterMs: options.staleAfterMs })
    }

    const fixtureRoot = options.fixtureRoot ?? findFixtureRoot()
    if (fixtureRoot !== undefined) {
      for (const fixtureName of discoverFixtures(fixtureRoot)) {
        const source = new FixtureStateSource(fixtureRoot, fixtureName, this.sourceOptions)
        this.sources.set(source.descriptor.id, source)
      }
    }
  }

  listSources(): readonly SnapshotSourceDescriptor[] {
    this.assertOpen()
    return [...this.sources.values()]
      .map((source) => source.descriptor)
      .sort((left, right) => left.id.localeCompare(right.id))
  }

  read(sourceId: string): Promise<StateSnapshot> {
    this.assertOpen()
    return this.getSource(sourceId).read()
  }

  projectRoot(sourceId: string): string {
    this.assertOpen()
    const descriptor = this.getSource(sourceId).descriptor
    if (descriptor.kind !== 'project' || descriptor.rootPath === undefined) {
      throw new Error('The selected state source is not a local project.')
    }
    return descriptor.rootPath
  }

  async selectProject(rootPath: string): Promise<SnapshotSourceDescriptor> {
    this.assertOpen()
    const source = await ProjectStateSource.create(rootPath, this.sourceOptions)

    if (this.selectedProjectId !== undefined && this.selectedProjectId !== source.descriptor.id) {
      this.sources.get(this.selectedProjectId)?.close()
      this.sources.delete(this.selectedProjectId)
    }
    this.sources.get(source.descriptor.id)?.close()
    this.sources.set(source.descriptor.id, source)
    this.selectedProjectId = source.descriptor.id
    return source.descriptor
  }

  watch(sourceId: string, listener: StateListener): () => void {
    this.assertOpen()
    return this.getSource(sourceId).watch(listener)
  }

  close(): void {
    if (this.closed) return
    this.closed = true
    for (const source of this.sources.values()) source.close()
    this.sources.clear()
    this.selectedProjectId = undefined
  }

  private getSource(sourceId: string): StateSource {
    const source = this.sources.get(sourceId)
    if (source === undefined) throw new Error(`Unknown state source: ${sourceId}`)
    return source
  }

  private assertOpen(): void {
    if (this.closed) throw new Error('StateHub is closed.')
  }
}

function discoverFixtures(fixtureRoot: string): string[] {
  try {
    return readdirSync(fixtureRoot, { withFileTypes: true })
      .filter((entry) => entry.isDirectory() && existsSync(join(fixtureRoot, entry.name, '.codentum')))
      .map((entry) => entry.name)
      .sort((left, right) => left.localeCompare(right))
  } catch {
    return []
  }
}

function findFixtureRoot(): string | undefined {
  const moduleDirectory = dirname(fileURLToPath(import.meta.url))
  const candidates = [
    resolve(process.cwd(), 'fixtures/golden-state'),
    resolve(process.cwd(), '../../fixtures/golden-state'),
    resolve(moduleDirectory, '../../../fixtures/golden-state'),
    resolve(moduleDirectory, '../../../../fixtures/golden-state')
  ]
  return candidates.find((candidate) => existsSync(candidate))
}
