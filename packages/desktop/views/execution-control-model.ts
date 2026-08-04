import type { WorkPacket } from "@codentum/contracts";

export type ExecutionMode = "auto" | "paused" | "stopped";
export type ExecutionModuleStatus = "done" | "current" | "upcoming";

export interface ExecutionModule {
  readonly id: string;
  readonly label: string;
  readonly description: string;
  readonly custom: boolean;
}

export interface ExecutionReceipt {
  readonly id: string;
  readonly action: ExecutionCommand["type"];
  readonly message: string;
  readonly revision: number;
}

export interface ExecutionSession {
  readonly packetId: string;
  readonly modules: readonly ExecutionModule[];
  readonly selectedModuleId: string;
  readonly currentModuleIndex: number;
  readonly mode: ExecutionMode;
  readonly retainMemory: boolean;
  readonly promptNotes: Readonly<Record<string, readonly string[]>>;
  readonly revision: number;
  readonly lastReceipt: ExecutionReceipt | null;
}

export type ExecutionCommand =
  | { readonly type: "pause" }
  | { readonly type: "resume" }
  | { readonly type: "stop"; readonly retainMemory: boolean }
  | { readonly type: "back" }
  | { readonly type: "append_prompt"; readonly text: string }
  | { readonly type: "insert_module"; readonly label: string };

export const FIXED_EXECUTION_MODULES = [
  {
    id: "prepare",
    label: "准备阶段",
    description: "加载任务信息并准备所需工具",
    custom: false,
  },
  {
    id: "agent",
    label: "Agent 执行",
    description: "执行任务并持续更新进度",
    custom: false,
  },
  {
    id: "converge",
    label: "收敛阶段",
    description: "整理执行结果并完成质量检查",
    custom: false,
  },
] as const satisfies readonly ExecutionModule[];

function initialModuleIndex(packet: WorkPacket): number {
  if (packet.state === "pending" || packet.state === "ready") {
    return 0;
  }
  if (packet.state === "running" || packet.state === "blocked") {
    return 1;
  }
  return 2;
}

function initialMode(packet: WorkPacket): ExecutionMode {
  if (packet.state === "blocked") {
    return "paused";
  }
  if (packet.state === "accepted" || packet.state === "rejected" || packet.state === "abandoned") {
    return "stopped";
  }
  return "auto";
}

export function createExecutionSession(packet: WorkPacket): ExecutionSession {
  const currentModuleIndex = initialModuleIndex(packet);
  const selectedModule = FIXED_EXECUTION_MODULES[currentModuleIndex];
  if (selectedModule === undefined) {
    throw new RangeError("执行模块索引越界");
  }

  return {
    packetId: packet.id,
    modules: [...FIXED_EXECUTION_MODULES],
    selectedModuleId: selectedModule.id,
    currentModuleIndex,
    mode: initialMode(packet),
    retainMemory: true,
    promptNotes: {},
    revision: 0,
    lastReceipt: null,
  };
}

function requireModuleIndex(session: ExecutionSession, moduleId: string): number {
  const index = session.modules.findIndex((module) => module.id === moduleId);
  if (index < 0) {
    throw new RangeError(`未知执行模块：${moduleId}`);
  }
  return index;
}

export function selectExecutionModule(
  session: ExecutionSession,
  moduleId: string,
): ExecutionSession {
  requireModuleIndex(session, moduleId);
  return { ...session, selectedModuleId: moduleId };
}

export function getExecutionModuleStatus(
  session: ExecutionSession,
  moduleId: string,
): ExecutionModuleStatus {
  const index = requireModuleIndex(session, moduleId);
  if (index < session.currentModuleIndex) {
    return "done";
  }
  return index === session.currentModuleIndex ? "current" : "upcoming";
}

function nextCustomModuleId(session: ExecutionSession): string {
  let index = 1;
  while (session.modules.some((module) => module.id === `custom-${index}`)) {
    index += 1;
  }
  return `custom-${index}`;
}

function withReceipt(
  session: ExecutionSession,
  command: ExecutionCommand,
  changes: Partial<Omit<ExecutionSession, "packetId" | "revision" | "lastReceipt">>,
  message: string,
): ExecutionSession {
  const revision = session.revision + 1;
  return {
    ...session,
    ...changes,
    revision,
    lastReceipt: {
      id: `cmd-${session.packetId}-${revision}`,
      action: command.type,
      message,
      revision,
    },
  };
}

export function applyExecutionCommand(
  session: ExecutionSession,
  command: ExecutionCommand,
): ExecutionSession {
  const selectedIndex = requireModuleIndex(session, session.selectedModuleId);

  switch (command.type) {
    case "pause":
      return withReceipt(session, command, { mode: "paused" }, "已设置：完成当前步骤后暂停");

    case "resume":
      return withReceipt(session, command, { mode: "auto" }, "已恢复默认自动执行");

    case "stop":
      return withReceipt(
        session,
        command,
        { mode: "stopped", retainMemory: command.retainMemory },
        command.retainMemory ? "已停止任务并保留本次记忆" : "已停止任务并丢弃本次临时记忆",
      );

    case "back": {
      const currentModuleIndex = Math.max(0, session.currentModuleIndex - 1);
      const target = session.modules[currentModuleIndex];
      if (target === undefined) {
        throw new RangeError("找不到可返回的执行模块");
      }
      return withReceipt(
        session,
        command,
        {
          currentModuleIndex,
          selectedModuleId: target.id,
          mode: "paused",
        },
        currentModuleIndex === session.currentModuleIndex
          ? "已经位于第一个模块"
          : `已返回 ${target.label}，等待确认后继续`,
      );
    }

    case "append_prompt": {
      const text = command.text.trim();
      if (text.length === 0) {
        throw new TypeError("追加 Prompt 不能为空");
      }
      const currentNotes = session.promptNotes[session.selectedModuleId] ?? [];
      return withReceipt(
        session,
        command,
        {
          promptNotes: {
            ...session.promptNotes,
            [session.selectedModuleId]: [...currentNotes, text],
          },
        },
        "Prompt 已加入所选模块",
      );
    }

    case "insert_module": {
      const label = command.label.trim();
      if (label.length === 0) {
        throw new TypeError("新增模块名称不能为空");
      }
      const module: ExecutionModule = {
        id: nextCustomModuleId(session),
        label,
        description: "由操作员指令加入，可随执行流程继续调整",
        custom: true,
      };
      const modules = [...session.modules];
      modules.splice(selectedIndex + 1, 0, module);
      const currentModuleIndex = selectedIndex < session.currentModuleIndex
        ? session.currentModuleIndex + 1
        : session.currentModuleIndex;
      return withReceipt(
        session,
        command,
        { modules, currentModuleIndex, selectedModuleId: module.id },
        `已在所选位置后加入模块：${label}`,
      );
    }
  }
}

export function parseModuleAddCommand(input: string): Extract<ExecutionCommand, { type: "insert_module" }> {
  const match = /^\/module\s+add\s+(.+?)\s*$/iu.exec(input.trim());
  const rawLabel = match?.[1]?.trim();
  if (rawLabel === undefined || rawLabel.length === 0) {
    throw new SyntaxError("指令格式：/module add <模块名称>");
  }
  const quoted = /^(?:"([^"]+)"|'([^']+)')$/u.exec(rawLabel);
  const label = (quoted?.[1] ?? quoted?.[2] ?? rawLabel).trim();
  if (label.length === 0) {
    throw new SyntaxError("模块名称不能为空");
  }
  return { type: "insert_module", label };
}
