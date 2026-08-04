import { useState } from "react";
import type { WorkPacket } from "@codentum/contracts";
import type { StateSnapshot } from "@desktop/data/state-source";
import { ExecutionControlDrawer } from "./ExecutionControlDrawer";
import { createExecutionSession } from "./execution-control-model";
import type { ExecutionSession } from "./execution-control-model";
import {
  createKanbanColumns,
  getBudgetUsagePercent,
} from "./kanban-model";

interface KanbanBoardProps {
  readonly snapshot: StateSnapshot | null;
  readonly loading: boolean;
}

const KIND_LABELS: Readonly<Record<WorkPacket["kind"], string>> = {
  design: "设计",
  contract: "规范",
  impl: "实现",
  test: "测试",
  review: "评审",
  integrate: "集成",
  spike: "探索",
  fix: "修复",
  evolve: "进化",
};

function formatCost(value: number, currency: string): string {
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
  }).format(value);
}

interface PacketCardProps {
  readonly locked: boolean;
  readonly onOpen: () => void;
  readonly packet: WorkPacket;
}

function PacketCard({ locked, onOpen, packet }: PacketCardProps): React.JSX.Element {
  const budgetPercent = getBudgetUsagePercent(packet);
  const budgetTone = budgetPercent > 85 ? "danger" : budgetPercent > 60 ? "warning" : "normal";

  return (
    <article
      aria-haspopup="dialog"
      aria-label={`打开 ${packet.id} 的执行控制`}
      className="packet-card"
      data-packet-id={packet.id}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen();
        }
      }}
      role="button"
      tabIndex={0}
    >
      <header className="packet-card-header">
        <span className="packet-kind">{KIND_LABELS[packet.kind]}</span>
        <span className="packet-role">{packet.role}</span>
      </header>

      <strong className="packet-id">{packet.id}</strong>

      {locked || packet.acceptance.kind === "manual" ? (
        <div className="packet-signals">
          {locked ? <span className="packet-signal lock">执行中</span> : null}
          {packet.acceptance.kind === "manual" ? (
            <span className="packet-signal manual">待人工确认</span>
          ) : null}
        </div>
      ) : null}

      <dl className="packet-facts">
        <div>
          <dt>负责范围</dt>
          <dd title={packet.ownsPaths.join(" · ")}>
            {packet.ownsPaths[0] ?? "无"}
            {packet.ownsPaths.length > 1 ? ` +${packet.ownsPaths.length - 1}` : ""}
          </dd>
        </div>
        <div>
          <dt>依赖</dt>
          <dd title={packet.deps.join(" · ")}>
            {packet.deps.length === 0 ? "无" : `${packet.deps.length} 个任务`}
          </dd>
        </div>
      </dl>

      <div className="packet-budget">
        <div className="packet-budget-label">
          <span>预算</span>
          <span>
            {formatCost(packet.budget.spentUsd, packet.budget.currency)} / {formatCost(packet.budget.limitUsd, packet.budget.currency)}
          </span>
        </div>
        <div className="packet-budget-track" aria-label={`预算已使用 ${Math.round(budgetPercent)}%`}>
          <span className={budgetTone} style={{ width: `${budgetPercent}%` }} />
        </div>
      </div>

      <footer className="packet-card-footer">
        <span>尝试 {packet.attempts} 次</span>
        <span className="packet-card-open-hint">打开模块 →</span>
      </footer>
    </article>
  );
}

interface PacketSelection {
  readonly sourceId: string;
  readonly packetId: string;
}

export function KanbanBoard({ snapshot, loading }: KanbanBoardProps): React.JSX.Element {
  const [selection, setSelection] = useState<PacketSelection | null>(null);
  const [sessions, setSessions] = useState<Readonly<Record<string, ExecutionSession>>>({});
  const columns = createKanbanColumns(snapshot?.packets ?? []);
  const lockedPacketIds = new Set(
    snapshot?.graph.ownership.locks.map((lock) => lock.heldBy) ?? [],
  );
  const selectedPacket = selection !== null && snapshot?.source.id === selection.sourceId
    ? snapshot.packets.find((packet) => packet.id === selection.packetId)
    : undefined;
  const selectedSessionKey = selection === null
    ? null
    : `${selection.sourceId}:${selection.packetId}`;
  const selectedSession = selectedSessionKey === null
    ? undefined
    : sessions[selectedSessionKey];

  const openPacket = (packet: WorkPacket): void => {
    if (snapshot === null) {
      return;
    }
    const sourceId = snapshot.source.id;
    const key = `${sourceId}:${packet.id}`;
    setSessions((current) => current[key] === undefined
      ? { ...current, [key]: createExecutionSession(packet) }
      : current);
    setSelection({ sourceId, packetId: packet.id });
  };

  return (
    <>
      <section aria-busy={loading} className="kanban-section" aria-label="任务状态看板">
        <div className="kanban-summary">
          <div>
            <span className="section-tag">AGENT KANBAN</span>
            <strong>{snapshot?.source.label ?? "正在加载任务"}</strong>
            <small>{loading ? "加载中…" : `${snapshot?.packets.length ?? 0} 个任务`}</small>
          </div>
          <div className="kanban-legend">
            <span><i className="legend-lock" />{snapshot?.graph.ownership.locks.length ?? 0} 个执行任务</span>
            <span><i className="legend-edge" />{snapshot?.graph.dependency.edges.length ?? 0} 个依赖关系</span>
          </div>
        </div>

        <div className="kanban-scroll" tabIndex={0}>
          <div className="kanban-board">
            {columns.map((column) => (
              <section className={`kanban-column tone-${column.tone}`} data-state={column.state} key={column.state}>
                <header className="kanban-column-header">
                  <div>
                    <span className="kanban-state-dot" />
                    <strong>{column.label}</strong>
                  </div>
                  <span className="kanban-count">{column.packets.length}</span>
                  <p>{column.description}</p>
                </header>

                <div className="kanban-card-list">
                  {column.packets.length === 0 ? (
                    <div className="kanban-empty">
                      <span>—</span>
                      <small>暂无任务</small>
                    </div>
                  ) : column.packets.map((packet) => (
                    <PacketCard
                      key={packet.id}
                      locked={lockedPacketIds.has(packet.id)}
                      onOpen={() => { openPacket(packet); }}
                      packet={packet}
                    />
                  ))}
                </div>
              </section>
            ))}
          </div>
        </div>
      </section>

      {selectedPacket === undefined || selectedSession === undefined || selectedSessionKey === null ? null : (
        <ExecutionControlDrawer
          onClose={() => { setSelection(null); }}
          onSessionChange={(nextSession) => {
            setSessions((current) => ({ ...current, [selectedSessionKey]: nextSession }));
          }}
          packet={selectedPacket}
          session={selectedSession}
        />
      )}
    </>
  );
}
