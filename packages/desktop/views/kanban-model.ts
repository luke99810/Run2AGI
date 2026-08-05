import type { PacketState, WorkPacket } from "@codentum/contracts";

export interface KanbanStateDefinition {
  readonly state: PacketState;
  readonly label: string;
  readonly description: string;
  readonly tone: string;
}

export interface KanbanColumn extends KanbanStateDefinition {
  readonly packets: readonly WorkPacket[];
}

/** 顺序来自冻结 PacketState 契约，不在界面里添加第九种状态。 */
export const KANBAN_STATES = [
  { state: "pending", label: "待排期", description: "等待依赖或排期", tone: "slate" },
  { state: "ready", label: "可认领", description: "满足准入条件", tone: "cyan" },
  { state: "running", label: "执行中", description: "持锁并正在工作", tone: "blue" },
  { state: "blocked", label: "已阻塞", description: "已释放锁并升级", tone: "red" },
  { state: "review", label: "评审中", description: "等待对抗评审", tone: "violet" },
  { state: "accepted", label: "已接受", description: "通过验收与门禁", tone: "green" },
  { state: "rejected", label: "已拒绝", description: "打回后可再次修复", tone: "orange" },
  { state: "abandoned", label: "已放弃", description: "终止且不再推进", tone: "gray" },
] as const satisfies readonly KanbanStateDefinition[];

export function createKanbanColumns(packets: readonly WorkPacket[]): readonly KanbanColumn[] {
  return KANBAN_STATES.map((definition) => ({
    ...definition,
    packets: packets
      .filter((packet) => packet.state === definition.state)
      .sort((left, right) => {
        const createdOrder = left.provenance.createdAt.localeCompare(right.provenance.createdAt);
        return createdOrder === 0 ? left.id.localeCompare(right.id) : createdOrder;
      }),
  }));
}

export function getBudgetUsagePercent(packet: WorkPacket): number {
  if (packet.budget.limitUsd <= 0) {
    return packet.budget.spentUsd > 0 ? 100 : 0;
  }
  return Math.max(0, Math.min(100, (packet.budget.spentUsd / packet.budget.limitUsd) * 100));
}
