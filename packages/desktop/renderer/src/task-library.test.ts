import { describe, expect, it } from 'vitest'
import type { StateSnapshot } from '../../shared/protocol'
import {
  appendTaskConversation,
  createTaskSession,
  historyForAgent,
  pluginOptionsFromMcp,
  searchTaskSessions,
  skillOptionsFromRoles,
  taskDraftScope,
  taskConversationEntries,
  taskRequestsValidation,
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
    expect(task.context).not.toHaveProperty('connectivityMode')
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

  it('searches local conversations by title, requirement text, and attachment name', () => {
    const base = createTaskSession('unassigned', { defaultAccessMode: 'read_only' }, new Date('2026-08-10T00:00:00.000Z'))
    const task = { ...updateTaskFromDraft(base, '实现一个本地文件分析工具'), attachmentNames: ['需求清单.xlsx'] }
    expect(searchTaskSessions([task], '文件分析')).toEqual([task])
    expect(searchTaskSessions([task], '需求清单.xlsx')).toEqual([task])
    expect(searchTaskSessions([task], '不存在')).toEqual([])
  })

  it('searches and exports the same local command, receipt, Worker event, and evidence timeline', () => {
    const base = createTaskSession('project:1234567890abcdef12345678', { defaultAccessMode: 'read_only' }, new Date('2026-08-14T00:00:00.000Z'))
    const task = appendTaskConversation(base, { id: 'cmd-1:command', kind: 'command', at: '2026-08-14T00:01:00.000Z', text: '向 coder 发送 submit_requirement', commandId: 'cmd-1', action: 'submit_requirement' })
    const snapshot = {
      requirements: [{ packetId: 'wp-demo', text: '实现搜索', submittedAt: '2026-08-14T00:02:00.000Z', commandId: 'cmd-1', taskId: task.id }],
      workers: [{ workerId: 'worker-1', packetId: 'wp-demo', role: 'coder', attempt: 1, state: 'completed', events: [{ seq: 1, kind: 'tool_result', at: '2026-08-14T00:03:00.000Z', payload: { summary: '已生成文件' } }] }],
      evidence: [{ ref: 'evidence:demo', packetId: 'wp-demo', role: 'coder', kind: 'build', verdict: 'pass', artifacts: ['dist/app.js'], at: '2026-08-14T00:04:00.000Z' }]
    } as unknown as StateSnapshot
    const entries = taskConversationEntries(task, snapshot)
    expect(entries.map((entry) => entry.kind)).toEqual(['command', 'receipt', 'agent', 'evidence'])
    expect(searchTaskSessions([task], '已生成文件', snapshot)).toEqual([task])
    expect(searchTaskSessions([task], 'evidence:demo', snapshot)).toEqual([task])
  })

  it('derives third-party application choices from B projections without claiming disconnected services are available', () => {
    expect(pluginOptionsFromMcp([{ id: 'github', name: 'GitHub', category: 'third-party-app', purpose: '代码托管', transport: 'stdio', status: 'disconnected', authentication: 'missing', tools: [] }])).toEqual([
      expect.objectContaining({ id: 'github', detail: '代码托管', availability: 'pending_runtime' })
    ])
  })

  it('toggles resource ids without duplicates', () => {
    expect(toggleSelection(['git'], 'browser')).toEqual(['git', 'browser'])
    expect(toggleSelection(['git', 'browser'], 'git')).toEqual(['browser'])
  })

  it('does not claim a Skill is available until B projects its manifest and SKILL.md', () => {
    expect(skillOptionsFromRoles([
      {
        id: 'coder',
        usesModel: true,
        writes: [],
        reads: [],
        tools: [],
        transitions: [],
        skills: [
          { id: 'frontend', scope: 'role', state: 'active' },
          { id: 'backend', scope: 'role', state: 'active' },
          { id: 'testing', scope: 'role', state: 'active' }
        ]
      },
      {
        id: 'reviewer',
        usesModel: true,
        writes: [],
        reads: [],
        tools: [],
        transitions: [],
        skills: [
          { id: 'review', scope: 'role', state: 'active' },
          { id: 'security', scope: 'role', state: 'active' }
        ]
      }
    ])).toEqual([
      expect.objectContaining({ id: 'backend', label: '后端实现', availability: 'pending_runtime' }),
      expect.objectContaining({ id: 'frontend', availability: 'pending_runtime' }),
      expect.objectContaining({ id: 'review', availability: 'pending_runtime' }),
      expect.objectContaining({ id: 'security', label: '安全审计', availability: 'pending_runtime' }),
      expect.objectContaining({ id: 'testing', availability: 'pending_runtime' })
    ])
  })

  it('only enables integration and validation for an explicit request', () => {
    expect(taskRequestsValidation({ title: '调整桌面布局', preview: '让对话区域占满可用宽度' })).toBe(false)
    expect(taskRequestsValidation({ title: '验证上传功能', preview: '运行文件导入测试并给出结果' })).toBe(true)
    expect(taskRequestsValidation({ title: 'Run QA', preview: 'verify the integration flow' })).toBe(true)
    expect(taskRequestsValidation({ title: '生成交付包', preview: '打包当前项目源码' })).toBe(true)
  })
})
