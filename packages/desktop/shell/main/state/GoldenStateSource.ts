import { readFile, readdir } from "node:fs/promises";
import { join } from "node:path";
import type {
  BudgetFile,
  DecisionRecord,
  Evidence,
  GraphFile,
  KnowledgeFile,
  WorkPacket,
} from "@codentum/contracts";
import type {
  StateSnapshot,
  StateSource,
  StateSourceDescriptor,
} from "@desktop/data/state-source";

const GOLDEN_SOURCES = [
  { id: "empty", label: "空项目", kind: "golden-state", readOnly: true },
  { id: "mid-flight", label: "开发进行中", kind: "golden-state", readOnly: true },
  { id: "blocked", label: "阻塞与待审批", kind: "golden-state", readOnly: true },
] as const satisfies readonly StateSourceDescriptor[];

function assertJsonObject(value: unknown, filePath: string): asserts value is object {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError(`${filePath} 的顶层必须是 JSON 对象`);
  }
}

async function readJsonObject<T extends object>(filePath: string): Promise<T> {
  const text = await readFile(filePath, "utf8");
  const parsed: unknown = JSON.parse(text);
  assertJsonObject(parsed, filePath);
  return parsed as T;
}

async function readJsonDirectory<T extends object>(directoryPath: string): Promise<readonly T[]> {
  const entries = await readdir(directoryPath, { withFileTypes: true });
  const jsonFiles = entries
    .filter((entry) => entry.isFile() && entry.name.endsWith(".json"))
    .map((entry) => entry.name)
    .sort((left, right) => left.localeCompare(right));

  return Promise.all(jsonFiles.map((fileName) => readJsonObject<T>(join(directoryPath, fileName))));
}

async function readJsonLines<T extends object>(filePath: string): Promise<readonly T[]> {
  const text = await readFile(filePath, "utf8");
  const lines = text.split(/\r?\n/u);
  const records: T[] = [];

  lines.forEach((line, index) => {
    const trimmed = line.trim();
    if (trimmed.length === 0) {
      return;
    }

    let parsed: unknown;
    try {
      parsed = JSON.parse(trimmed);
    } catch (error: unknown) {
      const detail = error instanceof Error ? error.message : String(error);
      throw new SyntaxError(`${filePath}:${index + 1} 不是合法 JSON：${detail}`);
    }
    assertJsonObject(parsed, `${filePath}:${index + 1}`);
    records.push(parsed as T);
  });

  return records;
}

/**
 * 只读 golden-state 实现。
 * sourceId 只能命中固定白名单，绝不把渲染进程传入的字符串拼成任意路径。
 */
export class GoldenStateSource implements StateSource {
  readonly #rootDirectory: string;

  constructor(rootDirectory: string) {
    this.#rootDirectory = rootDirectory;
  }

  async list(): Promise<readonly StateSourceDescriptor[]> {
    return GOLDEN_SOURCES;
  }

  async read(sourceId: string): Promise<StateSnapshot> {
    const source = GOLDEN_SOURCES.find((candidate) => candidate.id === sourceId);
    if (source === undefined) {
      throw new RangeError(`未知运行场景：${sourceId}`);
    }

    const stateDirectory = join(this.#rootDirectory, source.id, ".codentum");
    const [graph, packets, budget, decisions, evidence, knowledge] = await Promise.all([
      readJsonObject<GraphFile>(join(stateDirectory, "graph.json")),
      readJsonDirectory<WorkPacket>(join(stateDirectory, "packets")),
      readJsonObject<BudgetFile>(join(stateDirectory, "budget.json")),
      readJsonLines<DecisionRecord>(join(stateDirectory, "decisions.jsonl")),
      readJsonDirectory<Evidence>(join(stateDirectory, "evidence")),
      readJsonDirectory<KnowledgeFile>(join(stateDirectory, "knowledge")),
    ]);

    return {
      source,
      graph,
      packets,
      budget,
      decisions,
      evidence,
      knowledge,
      loadedAt: new Date().toISOString(),
    };
  }
}
