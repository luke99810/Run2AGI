import { describe, expect, it } from 'vitest'
import {
  createTaskSession,
  historyForAgent,
  taskDraftScope,
  toggleSelection,
  updateTaskFromDraft
} from './task-library'

describe('task library', () => {
  it('isolates every task under a stable project and task scope', () => {
    const task = createTaskSession(
      'project:1234567890abcdef12345678',
      { defaultAccessMode: 'workspace_write' },
      new Date('2026-08-10T00:00:00.000Z')
    )
    expect(taskDraftScope(task)).toMatch(/^project:1234567890abcdef12345678:task:[0-9a-f-]{36}$/u)
    expect(task.context.accessMode).toBe('workspace_write')
    expect(task.context.connectivityMode).toBe('local')
    expect(task.attachmentNames).toEqual([])
  })

  it('keeps previous task summaries searchable without including the active task', () => {
    const first = updateTaskFromDraft(
      createTaskSession('fixture:mid-flight', { defaultAccessMode: 'read_only' }, new Date('2026-08-09T00:00:00.000Z')),
      '实现一个可以管理订阅费用的软件',
      new Date('2026-08-09T01:00:00.000Z')
    )
    const second = updateTaskFromDraft(
      createTaskSession('fixture:mid-flight', { defaultAccessMode: 'read_only' }, new Date('2026-08-10T00:00:00.000Z')),
      '检查前一个任务的测试结果',
      new Date('2026-08-10T01:00:00.000Z')
    )
    expect(historyForAgent([first, second], second.id)).toEqual([
      expect.objectContaining({ taskId: first.id, summary: '实现一个可以管理订阅费用的软件' })
    ])
  })

  it('toggles resource ids without duplicates', () => {
    expect(toggleSelection(['git'], 'browser')).toEqual(['git', 'browser'])
    expect(toggleSelection(['git', 'browser'], 'git')).toEqual(['browser'])
  })
})
