import { describe, expect, it } from 'vitest'
import type { WorkerProjection } from '../shared/protocol'
import { latestCheckpointRef } from './CommandPanel'

function worker(events: WorkerProjection['events']): WorkerProjection {
  return {
    workerId: 'worker-1',
    packetId: 'packet-1',
    role: 'coder',
    attempt: 1,
    state: 'running',
    events
  }
}

describe('latestCheckpointRef', () => {
  it('uses only the latest authoritative checkpoint event', () => {
    expect(latestCheckpointRef(worker([
      { kind: 'checkpoint', at: '2026-08-13T08:00:00.000Z', seq: 2, payload: { path: 'checkpoints/0001.json' } },
      { kind: 'module', at: '2026-08-13T08:01:00.000Z', seq: 3, payload: { checkpointRef: 'not-authoritative.json' } },
      { kind: 'checkpoint', at: '2026-08-13T08:02:00.000Z', seq: 4, payload: { checkpointRef: 'checkpoints/0002.json' } }
    ]))).toBe('checkpoints/0002.json')
  })

  it('does not invent a checkpoint when the engine did not project one', () => {
    expect(latestCheckpointRef(worker([
      { kind: 'module', at: '2026-08-13T08:00:00.000Z', seq: 1, payload: { moduleId: 'implement' } }
    ]))).toBeUndefined()
  })
})
