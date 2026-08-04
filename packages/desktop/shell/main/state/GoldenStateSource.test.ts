import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { GoldenStateSource } from "./GoldenStateSource";

const currentDirectory = dirname(fileURLToPath(import.meta.url));
const goldenStateRoot = resolve(currentDirectory, "../../../../../fixtures/golden-state");

describe("GoldenStateSource", () => {
  const source = new GoldenStateSource(goldenStateRoot);

  it("只列出固定的只读数据源，不暴露文件路径", async () => {
    const sources = await source.list();

    expect(sources.map((item) => item.id)).toEqual(["empty", "mid-flight", "blocked"]);
    expect(sources.every((item) => item.readOnly)).toBe(true);
    expect(sources.every((item) => !("path" in item))).toBe(true);
  });

  it("把 empty 快照读取为空数组且保留数值 0", async () => {
    const snapshot = await source.read("empty");

    expect(snapshot.packets).toEqual([]);
    expect(snapshot.decisions).toEqual([]);
    expect(snapshot.evidence).toEqual([]);
    expect(snapshot.knowledge).toEqual([]);
    expect(snapshot.graph.dependency.nodes).toEqual([]);
    expect(snapshot.graph.ownership.version).toBe(0);
    expect(snapshot.budget.spentUsd).toBe(0);
  });

  it("逐文件和逐行读取 mid-flight 完整投影", async () => {
    const snapshot = await source.read("mid-flight");

    expect(snapshot.packets).toHaveLength(5);
    expect(snapshot.graph.dependency.edges).toHaveLength(6);
    expect(snapshot.graph.ownership.locks).toHaveLength(3);
    expect(snapshot.decisions).toHaveLength(10);
    expect(snapshot.evidence).toHaveLength(1);
    expect(snapshot.knowledge).toEqual([]);
    expect(snapshot.decisions[8]?.action).toBe("tool_blocked");
  });

  it("读取 blocked 的异常态、告警和证据", async () => {
    const snapshot = await source.read("blocked");

    expect(snapshot.packets).toHaveLength(5);
    expect(snapshot.packets.some((packet) => packet.state === "blocked")).toBe(true);
    expect(snapshot.decisions).toHaveLength(12);
    expect(snapshot.evidence).toHaveLength(4);
    expect(snapshot.budget.spentUsd).toBe(18.4);
    expect(snapshot.budget.alerts?.[0]?.level).toBe("warn");
  });

  it("拒绝未知 id 和路径穿越字符串", async () => {
    await expect(source.read("unknown")).rejects.toBeInstanceOf(RangeError);
    await expect(source.read("../mid-flight")).rejects.toBeInstanceOf(RangeError);
  });
});
