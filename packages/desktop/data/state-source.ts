import type {
  BudgetFile,
  DecisionRecord,
  Evidence,
  GraphFile,
  KnowledgeFile,
  WorkPacket,
} from "@codentum/contracts";

export type StateSourceKind = "golden-state" | "project";

/** 可供界面选择的数据源。这里刻意不暴露本机绝对路径。 */
export interface StateSourceDescriptor {
  readonly id: string;
  readonly label: string;
  readonly kind: StateSourceKind;
  readonly readOnly: true;
}

/**
 * 桌面端在一次读取中得到的完整、一致投影。
 *
 * graph.json 只保存节点 id、依赖边与路径锁；状态、角色和预算等任务详情
 * 必须通过 packets 按 id 关联，不能从 graph 中猜测。
 */
export interface StateSnapshot {
  readonly source: StateSourceDescriptor;
  readonly graph: GraphFile;
  readonly packets: readonly WorkPacket[];
  readonly budget: BudgetFile;
  readonly decisions: readonly DecisionRecord[];
  readonly evidence: readonly Evidence[];
  readonly knowledge: readonly KnowledgeFile[];
  readonly loadedAt: string;
}

/**
 * UI 唯一依赖的数据接口。没有 save/update/delete，从类型层面保持只读。
 * mock、golden-state 与未来真实项目数据源都必须实现同一接口。
 */
export interface StateSource {
  list(): Promise<readonly StateSourceDescriptor[]>;
  read(sourceId: string): Promise<StateSnapshot>;
}

export const STATE_IPC_CHANNELS = Object.freeze({
  list: "codentum:state:list",
  read: "codentum:state:read",
});
