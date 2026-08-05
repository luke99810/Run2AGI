import { useMemo, useState } from "react";
import type { CSSProperties } from "react";
import type { RoleId } from "@codentum/contracts";
import type { StateSnapshot } from "@desktop/data/state-source";
import {
  ROLE_LABELS,
  createOperationsModel,
} from "./operations-model";
import type {
  AgentStatusItem,
  CostRow,
  OperationsSection,
} from "./operations-model";

interface OperationsDashboardProps {
  readonly loading: boolean;
  readonly snapshot: StateSnapshot | null;
}

type AgentFilter = "all" | "active" | "attention" | "complete";
type CostDimension = "role" | "model";

const SECTION_TABS = [
  { id: "agents", label: "Agent 状态", short: "实时状态" },
  { id: "gantt", label: "甘特图", short: "执行波次" },
  { id: "dependencies", label: "依赖图", short: "任务关系" },
  { id: "cost", label: "成本仪表", short: "费用分布" },
] as const satisfies readonly {
  readonly id: OperationsSection;
  readonly label: string;
  readonly short: string;
}[];

const FILTERS = [
  { id: "all", label: "全部" },
  { id: "active", label: "执行中" },
  { id: "attention", label: "需关注" },
  { id: "complete", label: "已完成" },
] as const satisfies readonly { readonly id: AgentFilter; readonly label: string }[];

function formatUsd(value: number): string {
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
  }).format(value);
}

function filterAgents(agents: readonly AgentStatusItem[], filter: AgentFilter): readonly AgentStatusItem[] {
  switch (filter) {
    case "active": return agents.filter((agent) => agent.locked || agent.state === "running");
    case "attention": return agents.filter((agent) => agent.state === "blocked" || agent.state === "rejected");
    case "complete": return agents.filter((agent) => agent.state === "accepted");
    default: return agents;
  }
}

function costLabel(row: CostRow, dimension: CostDimension): string {
  if (dimension === "role" && Object.hasOwn(ROLE_LABELS, row.id)) {
    return ROLE_LABELS[row.id as RoleId];
  }
  return row.label;
}

export function OperationsDashboard({ loading, snapshot }: OperationsDashboardProps): React.JSX.Element {
  const [activeSection, setActiveSection] = useState<OperationsSection>("agents");
  const [agentFilter, setAgentFilter] = useState<AgentFilter>("all");
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [costDimension, setCostDimension] = useState<CostDimension>("role");
  const model = useMemo(() => createOperationsModel(snapshot), [snapshot]);
  const visibleAgents = filterAgents(model.agents, agentFilter);
  const selectedAgent = model.agents.find((agent) => agent.packetId === selectedAgentId)
    ?? visibleAgents[0];
  const selectedNode = model.dependencyNodes.find((node) => node.packetId === selectedNodeId)
    ?? model.dependencyNodes[0];
  const costRows = costDimension === "role" ? model.roleCosts : model.modelCosts;
  const selectedNodeAgent = selectedNode === undefined
    ? undefined
    : model.agents.find((agent) => agent.packetId === selectedNode.packetId);

  return (
    <section aria-busy={loading} className="operations-page" aria-labelledby="operations-title">
      <header className="operations-hero">
        <div className="operations-hero-copy">
          <span className="operations-kicker">AGENT OPERATIONS</span>
          <h1 id="operations-title">Agent 运行中心</h1>
          <p>从同一份运行快照查看团队状态、执行波次、任务依赖和成本变化。</p>
        </div>
        <div className="operations-live-chip">
          <span className="status-dot" />
          {loading ? "正在同步" : snapshot?.source.label ?? "等待数据"}
        </div>
        <div className="operations-summary-strip">
          <article>
            <span>Agent 任务</span>
            <strong>{model.agents.length}</strong>
          </article>
          <article>
            <span>正在执行</span>
            <strong>{model.activeAgents}</strong>
          </article>
          <article className={model.blockedAgents > 0 ? "has-alert" : ""}>
            <span>需要关注</span>
            <strong>{model.blockedAgents}</strong>
          </article>
          <article>
            <span>累计成本</span>
            <strong>{formatUsd(model.spentUsd)}</strong>
          </article>
        </div>
      </header>

      <nav className="operations-tabs" aria-label="运行中心模块">
        {SECTION_TABS.map((tab, index) => (
          <button
            aria-current={activeSection === tab.id ? "page" : undefined}
            className={activeSection === tab.id ? "is-active" : ""}
            key={tab.id}
            onClick={() => { setActiveSection(tab.id); }}
            type="button"
          >
            <span>0{index + 1}</span>
            <strong>{tab.label}</strong>
            <small>{tab.short}</small>
          </button>
        ))}
      </nav>

      <div className="operations-content">
        {activeSection === "agents" ? (
          <section className="agent-status-section" aria-labelledby="agent-status-title">
            <header className="operations-section-heading">
              <div>
                <span>LIVE TEAM</span>
                <h2 id="agent-status-title">Agent 状态板</h2>
              </div>
              <div className="agent-filter" role="group" aria-label="筛选 Agent 状态">
                {FILTERS.map((filter) => (
                  <button
                    aria-pressed={agentFilter === filter.id}
                    className={agentFilter === filter.id ? "is-active" : ""}
                    key={filter.id}
                    onClick={() => { setAgentFilter(filter.id); }}
                    type="button"
                  >
                    {filter.label}
                  </button>
                ))}
              </div>
            </header>

            {visibleAgents.length === 0 ? (
              <div className="operations-empty">
                <strong>当前筛选下没有 Agent</strong>
                <span>切换运行场景或选择其他状态查看。</span>
              </div>
            ) : (
              <div className="agent-status-layout">
                <div className="agent-status-list">
                  {visibleAgents.map((agent, index) => (
                    <button
                      aria-pressed={selectedAgent?.packetId === agent.packetId}
                      className={`agent-status-row state-${agent.state}${selectedAgent?.packetId === agent.packetId ? " is-selected" : ""}`}
                      key={agent.packetId}
                      onClick={() => { setSelectedAgentId(agent.packetId); }}
                      type="button"
                    >
                      <span className="agent-avatar">A{String(index + 1).padStart(2, "0")}</span>
                      <span className="agent-main-copy">
                        <small>{agent.packetId}</small>
                        <strong>{agent.roleLabel}</strong>
                        <em>{agent.focus}</em>
                      </span>
                      <span className="agent-model">{agent.model}</span>
                      <span className="agent-state">
                        <i />
                        {agent.stateLabel}
                      </span>
                    </button>
                  ))}
                </div>

                {selectedAgent === undefined ? null : (
                  <aside className={`agent-detail-panel state-${selectedAgent.state}`} aria-live="polite">
                    <div className="agent-detail-head">
                      <span className="agent-avatar large">AG</span>
                      <div>
                        <small>当前 Agent</small>
                        <h3>{selectedAgent.roleLabel}</h3>
                        <span>{selectedAgent.packetId}</span>
                      </div>
                    </div>
                    <dl className="agent-detail-grid">
                      <div><dt>当前状态</dt><dd>{selectedAgent.stateLabel}</dd></div>
                      <div><dt>执行焦点</dt><dd>{selectedAgent.focus}</dd></div>
                      <div><dt>依赖任务</dt><dd>{selectedAgent.dependencyCount} 个</dd></div>
                      <div><dt>执行尝试</dt><dd>{selectedAgent.attempts} 次</dd></div>
                    </dl>
                    <div className="agent-budget-line">
                      <span>任务成本</span>
                      <strong>{formatUsd(selectedAgent.budgetSpentUsd)} / {formatUsd(selectedAgent.budgetLimitUsd)}</strong>
                    </div>
                  </aside>
                )}
              </div>
            )}
          </section>
        ) : null}

        {activeSection === "gantt" ? (
          <section className="gantt-section" aria-labelledby="gantt-title">
            <header className="operations-section-heading">
              <div><span>EXECUTION WAVES</span><h2 id="gantt-title">依赖波次甘特图</h2></div>
              <p>按依赖先后排列任务，处于同一波次的 Agent 可以并行推进。</p>
            </header>

            {model.gantt.length === 0 ? (
              <div className="operations-empty"><strong>暂无排期任务</strong><span>载入任务后将自动生成执行波次。</span></div>
            ) : (
              <div className="gantt-chart">
                <div className="gantt-axis">
                  <span />
                  {Array.from({ length: model.waveCount }, (_, index) => <strong key={index}>波次 {index + 1}</strong>)}
                </div>
                <div className="gantt-lanes">
                  {model.gantt.map((lane) => (
                    <div className="gantt-lane" key={lane.packetId}>
                      <div className="gantt-lane-label"><strong>{lane.roleLabel}</strong><small>{lane.packetId}</small></div>
                      <div className="gantt-track">
                        <button
                          className={`gantt-bar state-${lane.state}`}
                          onClick={() => { setSelectedAgentId(lane.packetId); setActiveSection("agents"); }}
                          style={{ left: `${lane.startPercent}%`, width: `${lane.widthPercent}%` }}
                          type="button"
                        >
                          <span>{lane.stateLabel}</span>
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </section>
        ) : null}

        {activeSection === "dependencies" ? (
          <section className="dependency-section" aria-labelledby="dependency-title">
            <header className="operations-section-heading">
              <div><span>LIVE GRAPH</span><h2 id="dependency-title">任务依赖图</h2></div>
              <p>点击节点查看负责 Agent、当前状态和任务范围。</p>
            </header>

            {model.dependencyNodes.length === 0 ? (
              <div className="operations-empty"><strong>暂无依赖关系</strong><span>当前运行场景还没有任务节点。</span></div>
            ) : (
              <div className="dependency-layout">
                <div className="dependency-canvas">
                  <svg aria-hidden="true" className="dependency-lines" preserveAspectRatio="none" viewBox="0 0 100 100">
                    <defs>
                      <marker id="dependency-arrow" markerHeight="5" markerWidth="6" orient="auto" refX="5" refY="2.5">
                        <path d="M0,0 L5,2.5 L0,5 Z" />
                      </marker>
                    </defs>
                    {model.dependencyEdges.map((edge) => (
                      <line
                        key={edge.id}
                        markerEnd="url(#dependency-arrow)"
                        x1={edge.fromX + 4}
                        x2={edge.toX - 4}
                        y1={edge.fromY}
                        y2={edge.toY}
                      />
                    ))}
                  </svg>
                  {model.dependencyNodes.map((node) => (
                    <button
                      aria-pressed={selectedNode?.packetId === node.packetId}
                      className={`dependency-node state-${node.state}${selectedNode?.packetId === node.packetId ? " is-selected" : ""}`}
                      key={node.packetId}
                      onClick={() => { setSelectedNodeId(node.packetId); }}
                      style={{ left: `${node.xPercent}%`, top: `${node.yPercent}%` }}
                      type="button"
                    >
                      <span>{node.roleLabel}</span>
                      <strong>{node.packetId}</strong>
                      <small>{node.stateLabel}</small>
                    </button>
                  ))}
                </div>
                {selectedNode === undefined ? null : (
                  <aside className="dependency-detail" aria-live="polite">
                    <span>波次 {selectedNode.wave}</span>
                    <h3>{selectedNode.roleLabel}</h3>
                    <strong>{selectedNode.packetId}</strong>
                    <dl>
                      <div><dt>状态</dt><dd>{selectedNode.stateLabel}</dd></div>
                      <div><dt>任务范围</dt><dd>{selectedNodeAgent?.focus ?? "等待分配"}</dd></div>
                      <div><dt>上游依赖</dt><dd>{selectedNodeAgent?.dependencyCount ?? 0} 个</dd></div>
                    </dl>
                  </aside>
                )}
              </div>
            )}
          </section>
        ) : null}

        {activeSection === "cost" ? (
          <section className="cost-section" aria-labelledby="cost-title">
            <header className="operations-section-heading">
              <div><span>USD BUDGET</span><h2 id="cost-title">成本仪表</h2></div>
              <div className="cost-dimension-switch" role="group" aria-label="成本分组方式">
                <button className={costDimension === "role" ? "is-active" : ""} onClick={() => { setCostDimension("role"); }} type="button">按角色</button>
                <button className={costDimension === "model" ? "is-active" : ""} onClick={() => { setCostDimension("model"); }} type="button">按模型</button>
              </div>
            </header>

            <div className="cost-dashboard">
              <div className="cost-gauge-panel">
                <div
                  aria-label={`预算已使用 ${Math.round(model.budgetPercent)}%`}
                  className="cost-gauge"
                  role="img"
                  style={{ "--cost-progress": `${model.budgetPercent * 3.6}deg` } as CSSProperties}
                >
                  <div><strong>{Math.round(model.budgetPercent)}%</strong><span>预算使用</span></div>
                </div>
                <div className="cost-total">
                  <span>已花费</span>
                  <strong>{formatUsd(model.spentUsd)}</strong>
                  <small>总额度 {formatUsd(model.limitUsd)}</small>
                </div>
              </div>

              <div className="cost-ranking" aria-live="polite">
                <header><strong>{costDimension === "role" ? "角色成本分布" : "模型成本分布"}</strong><span>USD</span></header>
                {costRows.length === 0 ? (
                  <div className="operations-empty compact"><strong>暂无成本记录</strong></div>
                ) : costRows.map((row, index) => (
                  <div className="cost-row" key={row.id}>
                    <span className="cost-rank">{String(index + 1).padStart(2, "0")}</span>
                    <div className="cost-row-main">
                      <div><strong>{costLabel(row, costDimension)}</strong><span>{formatUsd(row.amountUsd)}</span></div>
                      <div className="cost-bar"><span style={{ width: `${row.percent}%` }} /></div>
                    </div>
                  </div>
                ))}
              </div>

              <div className="packet-cost-list">
                <header><strong>任务成本</strong><span>按花费排序</span></header>
                {[...model.agents]
                  .sort((left, right) => right.budgetSpentUsd - left.budgetSpentUsd)
                  .slice(0, 5)
                  .map((agent) => (
                    <button key={agent.packetId} onClick={() => { setSelectedAgentId(agent.packetId); setActiveSection("agents"); }} type="button">
                      <span><strong>{agent.roleLabel}</strong><small>{agent.packetId}</small></span>
                      <em>{formatUsd(agent.budgetSpentUsd)}</em>
                    </button>
                  ))}
              </div>
            </div>
          </section>
        ) : null}
      </div>
    </section>
  );
}
