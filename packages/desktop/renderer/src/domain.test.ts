import { describe, expect, it } from 'vitest'
import type { GraphFile, WorkPacket } from '@codentum/contracts'
import type { CapabilityMap, WorkerProjection } from '../../shared/protocol'
import { buildDependencyWaves, buildFlowBoard, createOperatorCommand, hasCapability, NAVIGATION, projectWorkerModules, ROLE_ROSTER, sameProjectPath, warningsForDisplay } from './domain'

function packet(id: string, deps: readonly string[] = []): WorkPacket {
  return {
    id: id as WorkPacket['id'],
    kind: 'impl',
    state: 'pending',
    role: 'coder',
    ownsPaths: [],
    readsPaths: [],
    deps: deps as WorkPacket['deps'],
    acceptance: { kind: 'test', predicate: 'npm test', authoredBy: 'qa' },
    budget: { currency: 'CNY', limitCny: 1, spentCny: 0, degradationChain: [] },
    attempts: 0,
    evidence: [],
    provenance: { createdBy: 'planner', createdAt: '2026-08-07T00:00:00.000Z' }
  }
}

describe('buildDependencyWaves', () => {
  it('uses the authoritative dependency graph to create parallel waves', () => {
    const packets = [packet('a'), packet('b'), packet('c'), packet('d')]
    const graph: GraphFile = {
      schemaVersion: 1,
      dependency: {
        nodes: packets.map((item) => item.id),
        edges: [
          { from: packets[0]!.id, to: packets[1]!.id },
          { from: packets[0]!.id, to: packets[2]!.id },
          { from: packets[1]!.id, to: packets[3]!.id },
          { from: packets[2]!.id, to: packets[3]!.id }
        ]
      },
      ownership: { locks: [], version: 1 }
    }
    expect(buildDependencyWaves({ graph, packets })).toEqual({
      waves: [['a'], ['b', 'c'], ['d']],
      unresolved: []
    })
  })

  it('reports a cycle instead of presenting it as a schedule', () => {
    const packets = [packet('a'), packet('b')]
    const graph: GraphFile = {
      schemaVersion: 1,
      dependency: {
        nodes: packets.map((item) => item.id),
        edges: [
          { from: packets[0]!.id, to: packets[1]!.id },
          { from: packets[1]!.id, to: packets[0]!.id }
        ]
      },
      ownership: { locks: [], version: 1 }
    }
    expect(buildDependencyWaves({ graph, packets })).toEqual({ waves: [], unresolved: ['a', 'b'] })
  })
})

describe('buildFlowBoard', () => {
  it('uses packet truth for counts and only displays projected WIP limits', () => {
    const packets = [
      { ...packet('ready-b'), state: 'ready' as const },
      { ...packet('ready-a'), state: 'ready' as const },
      { ...packet('running-a'), state: 'running' as const }
    ]
    const board = buildFlowBoard({
      packets,
      scheduling: {
        schemaVersion: 1,
        wipLimits: { running: 1 },
        readyQueue: ['ready-a', 'ready-b'],
        criticalPath: ['running-a']
      }
    })
    expect(board.find((column) => column.state === 'ready')).toMatchObject({ current: 2 })
    expect(board.find((column) => column.state === 'ready')).not.toHaveProperty('limit')
    expect(board.find((column) => column.state === 'ready')?.packets.map((item) => item.id)).toEqual(['ready-a', 'ready-b'])
    expect(board.find((column) => column.state === 'running')).toMatchObject({ current: 1, limit: 1, overLimit: false })
  })

  it('does not invent a WIP limit when scheduling data is absent', () => {
    const board = buildFlowBoard({ packets: [{ ...packet('running-a'), state: 'running' }], scheduling: null })
    expect(board.find((column) => column.state === 'running')).toMatchObject({ current: 1, overLimit: false })
    expect(board.find((column) => column.state === 'running')).not.toHaveProperty('limit')
  })
})

describe('warning presentation', () => {
  it('collapses derivative missing-file warnings for an uninitialized project', () => {
    const missingDirectory = '[missing] State directory is unavailable: C:\\project\\.codentum'
    expect(warningsForDisplay([
      missingDirectory,
      '[missing] Required state file is missing: graph.json',
      '[missing] Required state directory is missing: packets/'
    ])).toEqual([missingDirectory])
  })

  it('keeps specific missing-state warnings when the state directory exists', () => {
    const warnings = ['[missing] Required state file is missing: graph.json']
    expect(warningsForDisplay(warnings)).toEqual(warnings)
  })
})

describe('command guard and envelope', () => {
  it('matches canonical project paths without crossing operating-system semantics', () => {
    expect(sameProjectPath('C:\\Work\\Codentum\\', 'c:/work/codentum')).toBe(true)
    expect(sameProjectPath('/work/Codentum/', '/work/Codentum')).toBe(true)
    expect(sameProjectPath('/work/Codentum', '/work/codentum')).toBe(false)
    expect(sameProjectPath(undefined, '/work/Codentum')).toBe(false)
  })

  const capabilities = {
    requirements: true,
    planConfirmation: false,
    pauseAtSafePoint: false,
    resume: false,
    stop: true,
    keepMemory: false,
    forkFromCheckpoint: false,
    appendPrompt: false,
    insertModule: false
  } satisfies CapabilityMap

  it('exposes only actions backed by engine capabilities', () => {
    expect(hasCapability(capabilities, 'submit_requirement')).toBe(true)
    expect(hasCapability(capabilities, 'stop')).toBe(true)
    expect(hasCapability(capabilities, 'append_prompt')).toBe(false)
  })

  it('keeps command routing and revision in the IPC envelope', () => {
    expect(createOperatorCommand({
      commandId: 'cmd-1',
      runId: 'run-1',
      expectedRevision: 8,
      agentId: 'worker-1',
      packetId: 'packet-1',
      moduleId: 'test',
      action: 'pause_at_safe_point',
      requestedAt: '2026-08-07T00:00:00.000Z'
    })).toMatchObject({
      commandId: 'cmd-1',
      runId: 'run-1',
      expectedRevision: 8,
      target: { agentId: 'worker-1', packetId: 'packet-1', moduleId: 'test' },
      action: 'pause_at_safe_point',
      payload: {}
    })
  })
})

describe('role roster', () => {
  it('matches the frozen software-delivery role ids without inventing extra agents', () => {
    expect(ROLE_ROSTER.map((entry) => entry.id)).toEqual([
      'intake',
      'architect',
      'planner',
      'qa',
      'coder',
      'helper',
      'reviewer',
      'integrator',
      'manager',
      'evolver',
      'guardian'
    ])
  })
})

describe('management navigation', () => {
  it('exposes the MCP management module without claiming a runtime connection', () => {
    expect(NAVIGATION).toContainEqual({ id: 'mcp', label: 'MCP', icon: 'server' })
  })
})

describe('projectWorkerModules', () => {
  it('uses observed engine events and currentModule without inventing future modules', () => {
    const worker: WorkerProjection = {
      workerId: 'worker-1',
      packetId: 'packet-1',
      role: 'coder',
      attempt: 1,
      state: 'running',
      currentModule: 'test',
      events: [
        { seq: 1, kind: 'module_started', at: '2026-08-07T00:00:00Z', payload: { moduleId: 'implement', label: '实现' } },
        { seq: 2, kind: 'module_completed', at: '2026-08-07T00:01:00Z', payload: { moduleId: 'implement', label: '实现' } },
        { seq: 3, kind: 'module_started', at: '2026-08-07T00:02:00Z', payload: { moduleId: 'test', label: '测试' } }
      ]
    }
    expect(projectWorkerModules(worker)).toEqual([
      { id: 'implement', label: '实现', state: 'completed', firstSeq: 1, lastEventAt: '2026-08-07T00:01:00Z' },
      { id: 'test', label: '测试', state: 'running', firstSeq: 3, lastEventAt: '2026-08-07T00:02:00Z' }
    ])
  })

  it('does not label a waiting or aborted worker module as running', () => {
    const base: WorkerProjection = {
      workerId: 'worker-1',
      packetId: 'packet-1',
      role: 'coder',
      attempt: 1,
      state: 'waiting',
      currentModule: 'review',
      events: []
    }
    expect(projectWorkerModules(base)[0]?.state).toBe('waiting')
    expect(projectWorkerModules({ ...base, state: 'aborted' })[0]?.state).toBe('unknown')
  })
})
