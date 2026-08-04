import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { GoldenStateSource } from "../shell/main/state/GoldenStateSource";
import { createKanbanColumns, KANBAN_STATES } from "./kanban-model";

const currentDirectory = dirname(fileURLToPath(import.meta.url));
const goldenStateRoot = resolve(currentDirectory, "../../../fixtures/golden-state");
const source = new GoldenStateSource(goldenStateRoot);

function packetIdsByState(
  packets: Parameters<typeof createKanbanColumns>[0],
): Record<(typeof KANBAN_STATES)[number]["state"], readonly string[]> {
  const packetIds: Record<(typeof KANBAN_STATES)[number]["state"], string[]> = {
    pending: [],
    ready: [],
    running: [],
    blocked: [],
    review: [],
    accepted: [],
    rejected: [],
    abandoned: [],
  };

  createKanbanColumns(packets).forEach((column) => {
    packetIds[column.state] = column.packets.map((packet) => packet.id);
  });

  return packetIds;
}

describe("kanban model", () => {
  it("keeps the frozen eight-state column order", () => {
    expect(KANBAN_STATES.map((column) => column.state)).toEqual([
      "pending",
      "ready",
      "running",
      "blocked",
      "review",
      "accepted",
      "rejected",
      "abandoned",
    ]);
  });

  it("classifies the empty snapshot into eight empty columns", async () => {
    const snapshot = await source.read("empty");

    expect(packetIdsByState(snapshot.packets)).toEqual({
      pending: [],
      ready: [],
      running: [],
      blocked: [],
      review: [],
      accepted: [],
      rejected: [],
      abandoned: [],
    });
  });

  it("classifies every mid-flight packet by PacketState", async () => {
    const snapshot = await source.read("mid-flight");

    expect(packetIdsByState(snapshot.packets)).toEqual({
      pending: ["wp-e5g005"],
      ready: [],
      running: ["wp-b2d002", "wp-c3e003", "wp-d4f004"],
      blocked: [],
      review: [],
      accepted: ["wp-a1c001"],
      rejected: [],
      abandoned: [],
    });
  });

  it("uses state, not kind or acceptance type, for blocked snapshot columns", async () => {
    const snapshot = await source.read("blocked");
    const packetsByState = packetIdsByState(snapshot.packets);

    expect(packetsByState).toEqual({
      pending: ["wp-f6h006"],
      ready: [],
      running: ["wp-c3e003", "wp-d4f004"],
      blocked: ["wp-b2d002"],
      review: [],
      accepted: ["wp-a1c001"],
      rejected: [],
      abandoned: [],
    });

    const reviewPacket = snapshot.packets.find((packet) => packet.id === "wp-c3e003");
    expect(reviewPacket).toMatchObject({ kind: "review", state: "running" });
    expect(packetsByState.running).toContain("wp-c3e003");
    expect(packetsByState.review).not.toContain("wp-c3e003");

    const manualApprovalPacket = snapshot.packets.find((packet) => packet.id === "wp-f6h006");
    expect(manualApprovalPacket).toMatchObject({
      state: "pending",
      acceptance: { kind: "manual" },
    });
    expect(packetsByState.pending).toContain("wp-f6h006");
  });
});
