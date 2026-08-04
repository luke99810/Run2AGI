import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { GoldenStateSource } from "../shell/main/state/GoldenStateSource";
import { createOperationsModel } from "./operations-model";

const source = new GoldenStateSource(resolve(import.meta.dirname, "../../../fixtures/golden-state"));

describe("operations model", () => {
  it("为空快照生成稳定的空状态", async () => {
    const model = createOperationsModel(await source.read("empty"));
    expect(model.agents).toEqual([]);
    expect(model.dependencyNodes).toEqual([]);
    expect(model.spentUsd).toBe(0);
    expect(model.waveCount).toBe(1);
  });

  it("把开发中快照投影为 Agent、波次、依赖和成本", async () => {
    const model = createOperationsModel(await source.read("mid-flight"));
    expect(model.agents).toHaveLength(5);
    expect(model.activeAgents).toBe(3);
    expect(model.completedAgents).toBe(1);
    expect(model.waveCount).toBe(3);
    expect(model.gantt.filter((lane) => lane.wave === 2)).toHaveLength(3);
    expect(model.dependencyEdges).toHaveLength(6);
    expect(model.roleCosts[0]).toMatchObject({ id: "coder", amountUsd: 2.74 });
    expect(model.modelCosts[0]).toMatchObject({ id: "claude-opus-5", amountUsd: 3.65 });
    expect(model.budgetPercent).toBeCloseTo(24.1);
  });

  it("保留阻塞状态并且不会把它计入运行 Agent", async () => {
    const model = createOperationsModel(await source.read("blocked"));
    expect(model.blockedAgents).toBeGreaterThan(0);
    expect(model.agents.some((agent) => agent.state === "blocked")).toBe(true);
    expect(model.dependencyEdges.length).toBeGreaterThan(0);
  });
});
