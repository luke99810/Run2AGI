import type {
  PacketState,
  RoleId,
  WorkPacket,
} from "@codentum/contracts";
import type { StateSnapshot } from "@desktop/data/state-source";

export type OperationsSection = "agents" | "gantt" | "dependencies" | "cost";

export interface AgentStatusItem {
  readonly packetId: string;
  readonly role: RoleId;
  readonly roleLabel: string;
  readonly state: PacketState;
  readonly stateLabel: string;
  readonly focus: string;
  readonly model: string;
  readonly locked: boolean;
  readonly attempts: number;
  readonly dependencyCount: number;
  readonly budgetSpentUsd: number;
  readonly budgetLimitUsd: number;
}

export interface GanttLane {
  readonly packetId: string;
  readonly roleLabel: string;
  readonly state: PacketState;
  readonly stateLabel: string;
  readonly wave: number;
  readonly startPercent: number;
  readonly widthPercent: number;
}

export interface DependencyNodeLayout {
  readonly packetId: string;
  readonly roleLabel: string;
  readonly state: PacketState;
  readonly stateLabel: string;
  readonly wave: number;
  readonly xPercent: number;
  readonly yPercent: number;
}

export interface DependencyEdgeLayout {
  readonly id: string;
  readonly from: string;
  readonly to: string;
  readonly fromX: number;
  readonly fromY: number;
  readonly toX: number;
  readonly toY: number;
}

export interface CostRow {
  readonly id: string;
  readonly label: string;
  readonly amountUsd: number;
  readonly percent: number;
}

export interface OperationsModel {
  readonly agents: readonly AgentStatusItem[];
  readonly activeAgents: number;
  readonly blockedAgents: number;
  readonly completedAgents: number;
  readonly gantt: readonly GanttLane[];
  readonly waveCount: number;
  readonly dependencyNodes: readonly DependencyNodeLayout[];
  readonly dependencyEdges: readonly DependencyEdgeLayout[];
  readonly roleCosts: readonly CostRow[];
  readonly modelCosts: readonly CostRow[];
  readonly spentUsd: number;
  readonly limitUsd: number;
  readonly budgetPercent: number;
}

export const ROLE_LABELS: Readonly<Record<RoleId, string>> = {
  intake: "需求 Agent",
  architect: "架构 Agent",
  planner: "规划 Agent",
  qa: "质保 Agent",
  coder: "开发 Agent",
  helper: "协助 Agent",
  reviewer: "评审 Agent",
  integrator: "集成 Agent",
  manager: "管理 Agent",
  evolver: "进化 Agent",
  guardian: "守护 Agent",
};

export const STATE_LABELS: Readonly<Record<PacketState, string>> = {
  pending: "等待中",
  ready: "可执行",
  running: "执行中",
  blocked: "已阻塞",
  review: "评审中",
  accepted: "已完成",
  rejected: "待返工",
  abandoned: "已停止",
};

const STATE_ORDER: Readonly<Record<PacketState, number>> = {
  running: 0,
  blocked: 1,
  review: 2,
  ready: 3,
  pending: 4,
  rejected: 5,
  accepted: 6,
  abandoned: 7,
};

function calculateWaves(snapshot: StateSnapshot): ReadonlyMap<string, number> {
  const nodeIds = snapshot.graph.dependency.nodes.map(String);
  const nodeSet = new Set(nodeIds);
  const indegree = new Map(nodeIds.map((id) => [id, 0]));
  const nextByNode = new Map(nodeIds.map((id) => [id, [] as string[]]));

  snapshot.graph.dependency.edges.forEach((edge) => {
    const from = String(edge.from);
    const to = String(edge.to);
    if (!nodeSet.has(from) || !nodeSet.has(to)) return;
    nextByNode.get(from)?.push(to);
    indegree.set(to, (indegree.get(to) ?? 0) + 1);
  });

  const waves = new Map(nodeIds.map((id) => [id, 1]));
  const queue = nodeIds.filter((id) => indegree.get(id) === 0).sort();
  let cursor = 0;

  while (cursor < queue.length) {
    const current = queue[cursor];
    cursor += 1;
    if (current === undefined) continue;
    const currentWave = waves.get(current) ?? 1;
    (nextByNode.get(current) ?? []).sort().forEach((next) => {
      waves.set(next, Math.max(waves.get(next) ?? 1, currentWave + 1));
      const nextDegree = (indegree.get(next) ?? 1) - 1;
      indegree.set(next, nextDegree);
      if (nextDegree === 0) queue.push(next);
    });
  }

  return waves;
}

function costRows(values: Readonly<Record<string, number>> | undefined, spentUsd: number): readonly CostRow[] {
  return Object.entries(values ?? {})
    .map(([id, amountUsd]) => ({
      id,
      label: id,
      amountUsd,
      percent: spentUsd <= 0 ? 0 : Math.min(100, Math.max(0, (amountUsd / spentUsd) * 100)),
    }))
    .sort((left, right) => right.amountUsd - left.amountUsd || left.id.localeCompare(right.id));
}

function deriveRoleCosts(packets: readonly WorkPacket[]): Readonly<Record<string, number>> {
  const result: Record<string, number> = {};
  packets.forEach((packet) => {
    result[packet.role] = (result[packet.role] ?? 0) + packet.budget.spentUsd;
  });
  return result;
}

function deriveModelCosts(packets: readonly WorkPacket[]): Readonly<Record<string, number>> {
  const result: Record<string, number> = {};
  packets.forEach((packet) => {
    const model = packet.routing?.model ?? "未指定模型";
    result[model] = (result[model] ?? 0) + packet.budget.spentUsd;
  });
  return result;
}

export function createOperationsModel(snapshot: StateSnapshot | null): OperationsModel {
  if (snapshot === null) {
    return {
      agents: [], activeAgents: 0, blockedAgents: 0, completedAgents: 0,
      gantt: [], waveCount: 1, dependencyNodes: [], dependencyEdges: [],
      roleCosts: [], modelCosts: [], spentUsd: 0, limitUsd: 0, budgetPercent: 0,
    };
  }

  const lockedIds = new Set(snapshot.graph.ownership.locks.map((lock) => String(lock.heldBy)));
  const packetById = new Map(snapshot.packets.map((packet) => [String(packet.id), packet]));
  const waves = calculateWaves(snapshot);
  const waveCount = Math.max(1, ...Array.from(waves.values()));
  const agents = snapshot.packets
    .map((packet): AgentStatusItem => ({
      packetId: String(packet.id),
      role: packet.role,
      roleLabel: ROLE_LABELS[packet.role],
      state: packet.state,
      stateLabel: STATE_LABELS[packet.state],
      focus: packet.ownsPaths[0] ?? "等待分配",
      model: packet.routing?.model ?? "规则执行",
      locked: lockedIds.has(String(packet.id)),
      attempts: packet.attempts,
      dependencyCount: packet.deps.length,
      budgetSpentUsd: packet.budget.spentUsd,
      budgetLimitUsd: packet.budget.limitUsd,
    }))
    .sort((left, right) => STATE_ORDER[left.state] - STATE_ORDER[right.state]
      || left.packetId.localeCompare(right.packetId));

  const barUnit = 100 / waveCount;
  const gantt = agents
    .map((agent): GanttLane => {
      const wave = waves.get(agent.packetId) ?? 1;
      return {
        packetId: agent.packetId,
        roleLabel: agent.roleLabel,
        state: agent.state,
        stateLabel: agent.stateLabel,
        wave,
        startPercent: (wave - 1) * barUnit,
        widthPercent: Math.max(8, barUnit * 0.82),
      };
    })
    .sort((left, right) => left.wave - right.wave || left.packetId.localeCompare(right.packetId));

  const nodesByWave = new Map<number, string[]>();
  snapshot.graph.dependency.nodes.map(String).forEach((id) => {
    const wave = waves.get(id) ?? 1;
    nodesByWave.set(wave, [...(nodesByWave.get(wave) ?? []), id]);
  });

  const dependencyNodes = snapshot.graph.dependency.nodes.map(String).map((packetId): DependencyNodeLayout => {
    const wave = waves.get(packetId) ?? 1;
    const peers = (nodesByWave.get(wave) ?? []).sort();
    const peerIndex = Math.max(0, peers.indexOf(packetId));
    const packet = packetById.get(packetId);
    const xPercent = waveCount === 1 ? 50 : 8 + ((wave - 1) / (waveCount - 1)) * 84;
    const yPercent = peers.length <= 1 ? 50 : 14 + (peerIndex / (peers.length - 1)) * 72;
    return {
      packetId,
      roleLabel: packet === undefined ? "未知 Agent" : ROLE_LABELS[packet.role],
      state: packet?.state ?? "pending",
      stateLabel: STATE_LABELS[packet?.state ?? "pending"],
      wave,
      xPercent,
      yPercent,
    };
  });

  const nodeLayoutById = new Map(dependencyNodes.map((node) => [node.packetId, node]));
  const dependencyEdges = snapshot.graph.dependency.edges.flatMap((edge): readonly DependencyEdgeLayout[] => {
    const from = nodeLayoutById.get(String(edge.from));
    const to = nodeLayoutById.get(String(edge.to));
    if (from === undefined || to === undefined) return [];
    return [{
      id: `${from.packetId}->${to.packetId}`,
      from: from.packetId,
      to: to.packetId,
      fromX: from.xPercent,
      fromY: from.yPercent,
      toX: to.xPercent,
      toY: to.yPercent,
    }];
  });

  const roleValues = snapshot.budget.byRole ?? deriveRoleCosts(snapshot.packets);
  const modelValues = snapshot.budget.byModel ?? deriveModelCosts(snapshot.packets);
  const budgetPercent = snapshot.budget.limitUsd <= 0
    ? (snapshot.budget.spentUsd > 0 ? 100 : 0)
    : Math.min(100, Math.max(0, (snapshot.budget.spentUsd / snapshot.budget.limitUsd) * 100));

  return {
    agents,
    activeAgents: agents.filter((agent) => agent.locked || agent.state === "running").length,
    blockedAgents: agents.filter((agent) => agent.state === "blocked").length,
    completedAgents: agents.filter((agent) => agent.state === "accepted").length,
    gantt,
    waveCount,
    dependencyNodes,
    dependencyEdges,
    roleCosts: costRows(roleValues, snapshot.budget.spentUsd),
    modelCosts: costRows(modelValues, snapshot.budget.spentUsd),
    spentUsd: snapshot.budget.spentUsd,
    limitUsd: snapshot.budget.limitUsd,
    budgetPercent,
  };
}
