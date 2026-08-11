import type { ReactNode } from 'react'
import { EmptyState, Icon, PageHeader } from '../panels/Common'

export type McpServiceStatus = 'connected' | 'connecting' | 'disconnected' | 'error'

export interface McpServiceProjection {
  readonly id: string
  readonly name: string
  readonly transport: 'stdio' | 'http' | 'sse'
  readonly status: McpServiceStatus
  readonly authentication: 'not_required' | 'configured' | 'missing' | 'unknown'
  readonly tools: readonly string[]
  readonly error?: string
}

const STATUS_LABELS: Readonly<Record<McpServiceStatus, string>> = {
  connected: '已连接',
  connecting: '连接中',
  disconnected: '未连接',
  error: '连接错误'
}

const AUTH_LABELS: Readonly<Record<McpServiceProjection['authentication'], string>> = {
  not_required: '无需鉴权',
  configured: '已配置',
  missing: '缺少凭据',
  unknown: '未知'
}

export function McpView({ services }: { readonly services: readonly McpServiceProjection[] }): ReactNode {
  const connectedCount = services.filter((service) => service.status === 'connected').length
  const toolCount = services
    .filter((service) => service.status === 'connected')
    .reduce((total, service) => total + service.tools.length, 0)

  return (
    <main className="page mcp-page">
      <PageHeader
        eyebrow="管理模块"
        title="MCP 服务"
        description="查看 Agent 可用的 MCP 服务、工具、鉴权状态和连接错误。"
      />

      <section className="mcp-summary" aria-label="MCP 概览">
        <div><span>服务</span><strong>{services.length}</strong></div>
        <div><span>已连接</span><strong>{connectedCount}</strong></div>
        <div><span>可用工具</span><strong>{toolCount}</strong></div>
        <div><span>配置来源</span><strong>尚未接入</strong></div>
      </section>

      <section className="mcp-service-section" aria-labelledby="mcp-service-heading">
        <header>
          <div>
            <span className="section-icon"><Icon name="server" size={19} /></span>
            <div><h2 id="mcp-service-heading">服务</h2><p>状态只来自运行时投影，不由前端推断。</p></div>
          </div>
          <span className="mcp-runtime-state">运行时未提供 MCP 投影</span>
        </header>

        {services.length === 0 ? (
          <EmptyState
            icon="server"
            title="尚未配置 MCP 服务"
            detail="当前引擎没有返回 MCP 服务清单；连接和配置操作保持不可用。"
          />
        ) : (
          <div className="mcp-service-list">
            <div className="mcp-service-columns" aria-hidden="true">
              <span>服务</span><span>传输</span><span>状态</span><span>鉴权</span><span>工具</span>
            </div>
            {services.map((service) => (
              <article className="mcp-service-row" key={service.id}>
                <div><strong>{service.name}</strong><small>{service.id}</small></div>
                <code>{service.transport}</code>
                <span className={`mcp-status mcp-status-${service.status}`}>{STATUS_LABELS[service.status]}</span>
                <span>{AUTH_LABELS[service.authentication]}</span>
                <span>{service.tools.length}</span>
                {service.error === undefined ? null : <p role="alert">{service.error}</p>}
              </article>
            ))}
          </div>
        )}
      </section>

      <p className="mcp-boundary"><Icon name="shield" size={16} />MCP 只提供工具入口；每个 Agent 的实际权限仍由 RoleSpec、ToolSurface 与 Guardian 收紧。</p>
    </main>
  )
}
