import { basename, join, resolve } from 'node:path'
import type { SnapshotSourceDescriptor } from '../shared/protocol'
import { DirectoryStateSource, type DirectoryStateSourceOptions } from './directory-state-source'

export class FixtureStateSource extends DirectoryStateSource {
  readonly fixtureName: string

  constructor(fixtureRoot: string, fixtureName: string, options: DirectoryStateSourceOptions = {}) {
    if (fixtureName.length === 0 || basename(fixtureName) !== fixtureName || fixtureName === '.' || fixtureName === '..') {
      throw new Error(`Invalid fixture name: ${fixtureName}`)
    }

    const projectRoot = resolve(fixtureRoot, fixtureName)
    const descriptor: SnapshotSourceDescriptor = {
      id: `fixture:${fixtureName}`,
      kind: 'fixture',
      label: fixtureName,
      rootPath: projectRoot
    }
    super(descriptor, join(projectRoot, '.codentum'), {
      ...options,
      detectStaleWorkers: false
    })
    this.fixtureName = fixtureName
  }
}
