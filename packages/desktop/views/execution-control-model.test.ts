import { describe, expect, it } from "vitest";
import type { WorkPacket } from "@codentum/contracts";
import {
  applyExecutionCommand,
  createExecutionSession,
  FIXED_EXECUTION_MODULES,
  getExecutionModuleStatus,
  parseModuleAddCommand,
  selectExecutionModule,
} from "./execution-control-model";

const runningPacket = {
  id: "wp-test001",
  state: "running",
} as WorkPacket;

describe("execution control draft model", () => {
  it("starts with the three fixed Harness modules", () => {
    const session = createExecutionSession(runningPacket);

    expect(session.modules).toEqual(FIXED_EXECUTION_MODULES);
    expect(session.selectedModuleId).toBe("agent");
    expect(getExecutionModuleStatus(session, "prepare")).toBe("done");
    expect(getExecutionModuleStatus(session, "agent")).toBe("current");
    expect(getExecutionModuleStatus(session, "converge")).toBe("upcoming");
  });

  it("selects modules and applies pause, resume, and stop with receipts", () => {
    const selected = selectExecutionModule(createExecutionSession(runningPacket), "converge");
    const paused = applyExecutionCommand(selected, { type: "pause" });
    const resumed = applyExecutionCommand(paused, { type: "resume" });
    const stopped = applyExecutionCommand(resumed, { type: "stop", retainMemory: false });

    expect(selected.selectedModuleId).toBe("converge");
    expect(paused.mode).toBe("paused");
    expect(resumed.mode).toBe("auto");
    expect(stopped).toMatchObject({ mode: "stopped", retainMemory: false, revision: 3 });
    expect(stopped.lastReceipt).toMatchObject({
      id: "cmd-wp-test001-3",
      action: "stop",
      revision: 3,
    });
  });

  it("returns to the previous module without crossing the first module", () => {
    const session = createExecutionSession(runningPacket);
    const previous = applyExecutionCommand(session, { type: "back" });
    const boundary = applyExecutionCommand(previous, { type: "back" });

    expect(previous).toMatchObject({ currentModuleIndex: 0, selectedModuleId: "prepare", mode: "paused" });
    expect(boundary.currentModuleIndex).toBe(0);
    expect(boundary.lastReceipt?.message).toBe("已经位于第一个模块");
  });

  it("appends prompts and inserts modules after the selected module", () => {
    const selected = selectExecutionModule(createExecutionSession(runningPacket), "agent");
    const withPrompt = applyExecutionCommand(selected, {
      type: "append_prompt",
      text: "  先检查并发边界  ",
    });
    const inserted = applyExecutionCommand(withPrompt, {
      type: "insert_module",
      label: "安全复核",
    });
    const insertedAgain = applyExecutionCommand(inserted, {
      type: "insert_module",
      label: "交付检查",
    });

    expect(withPrompt.promptNotes["agent"]).toEqual(["先检查并发边界"]);
    expect(inserted.modules.map((module) => module.id)).toEqual([
      "prepare",
      "agent",
      "custom-1",
      "converge",
    ]);
    expect(inserted.selectedModuleId).toBe("custom-1");
    expect(insertedAgain.modules.map((module) => module.id)).toContain("custom-2");
  });

  it("parses module commands and rejects empty input", () => {
    expect(parseModuleAddCommand('/module add "安全复核"')).toEqual({
      type: "insert_module",
      label: "安全复核",
    });
    expect(() => parseModuleAddCommand("/module add")).toThrow("指令格式");
    expect(() => applyExecutionCommand(createExecutionSession(runningPacket), {
      type: "append_prompt",
      text: "   ",
    })).toThrow("不能为空");
  });
});
