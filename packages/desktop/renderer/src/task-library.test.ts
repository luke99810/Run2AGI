import { describe, expect, it } from 'vitest'
import {
  createTaskSession,
  historyForAgent,
  pluginOptionsFromMcpServices,
  searchTaskSessions,
  skillOptionsFromRoles,
  taskDraftScope,
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

  it('toggles resource ids without duplicates', () => {
    expect(toggleSelection(['git'], 'browser')).toEqual(['git', 'browser'])
    expect(toggleSelection(['git', 'browser'], 'git')).toEqual(['browser'])
  })

  it('derives available Skill options from projected RoleSpec bindings', () => {
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
      expect.objectContaining({ id: 'backend', label: '后端实现', availability: 'available', detail: 'B RoleSpec 已绑定：coder' }),
      expect.objectContaining({ id: 'frontend', availability: 'available', detail: 'B RoleSpec 已绑定：coder' }),
      expect.objectContaining({ id: 'review', availability: 'available', detail: 'B RoleSpec 已绑定：reviewer' }),
      expect.objectContaining({ id: 'security', label: '安全审计', availability: 'available', detail: 'B RoleSpec 已绑定：reviewer' }),
      expect.objectContaining({ id: 'testing', availability: 'available', detail: 'B RoleSpec 已绑定：coder' })
    ])
  })

  it('uses a truthful Browser plugin fallback before MCP projection exists', () => {
    const browser = pluginOptionsFromMcpServices(undefined).find((option) => option.id === 'browser')
    expect(browser).toEqual(expect.objectContaining({
      label: '浏览器',
      availability: 'pending_runtime'
    }))
    expect(browser?.detail).toContain('浏览器 MCP 服务尚未连接')
    expect(browser?.detail).not.toContain('等待 B')
  })

  it('derives plugin availability from the MCP runtime projection', () => {
    expect(pluginOptionsFromMcpServices([
      {
        id: 'filesystem',
        name: '本地文件系统',
        transport: 'stdio',
        status: 'connected',
        authentication: 'not_required',
        tools: ['read_file', 'list_directory', 'stat']
      },
      {
        id: 'git',
        name: 'Git 仓库',
        transport: 'stdio',
        status: 'connected',
        authentication: 'not_required',
        tools: ['status', 'diff']
      },
      {
        id: 'browser',
        name: '浏览器自动化',
        transport: 'stdio',
        status: 'disconnected',
        authentication: 'unknown',
        tools: ['navigate', 'screenshot'],
        error: '本地 browser MCP server 尚未启动'
      }
    ])).toEqual([
      expect.objectContaining({ id: 'local-files', label: '本地文件', availability: 'available', detail: expect.stringContaining('3 个工具') }),
      expect.objectContaining({ id: 'git', label: 'Git', availability: 'available', detail: expect.stringContaining('2 个工具') }),
      expect.objectContaining({ id: 'browser', label: '浏览器', availability: 'pending_runtime', detail: '本地 browser MCP server 尚未启动' })
    ])
  })

  it('only enables integration and validation for an explicit request', () => {
    expect(taskRequestsValidation({ title: '调整桌面布局', preview: '让对话区域占满可用宽度' })).toBe(false)
    expect(taskRequestsValidation({ title: '验证上传功能', preview: '运行文件导入测试并给出结果' })).toBe(true)
    expect(taskRequestsValidation({ title: 'Run QA', preview: 'verify the integration flow' })).toBe(true)
  })
})
