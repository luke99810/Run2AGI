#!/usr/bin/env python
"""validate_fixtures —— 用 schema 校验 fixtures/ 下的全部固件

════════════════════════════════════════════════════════════════
 为什么这个脚本必须进 CI
════════════════════════════════════════════════════════════════

固件与契约脱节 → 基于它写的测试全部失去意义，【而且不报错】。
桌面端会照着一份过时的快照渲染，测试全绿，接真数据时全炸。

════════════════════════════════════════════════════════════════
 除了 schema，还查八项 schema 表达不了的交叉约束
════════════════════════════════════════════════════════════════

  I1          同一快照内 running packet 的 ownsPaths 不得相交
  验收制衡     acceptance.authoredBy ≠ packet.role
  锁的合法性    ownership.locks 的 heldBy 必须是 running 的 packet
  依赖图无环    必须是 DAG
  图文件一致    graph.json 的节点 ↔ packets/ 的文件
  I6 审计链     同一 packet 的证据必须首尾相接
  证据引用有效  packet.evidence 指向的证据必须存在
  无凭证       固件里不许有真密钥

JSON Schema 表达不了跨文件的关系约束，而这几条恰恰是全案的核心不变量。
固件是它们的第一道验证场 —— 连手工造的快照都违反不变量，
说明不变量本身没想清楚。

依赖：零。用法：python scripts/validate_fixtures.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from lib.console import setup_console  # noqa: E402
from lib.schema import load_schemas, validate  # noqa: E402

setup_console()

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT / "packages" / "contracts" / "schemas"
GOLDEN = ROOT / "fixtures" / "golden-state"
CHAIN_HEAD = "sha256:" + "0" * 64

SECRET_RE = re.compile(
    r"\b(sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)

schemas = load_schemas(SCHEMA_DIR)
errors: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)


def read_json(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def list_json(d: Path) -> list[Path]:
    return sorted(d.glob("*.json")) if d.is_dir() else []


def check(label: str, schema_file: str, value: Any) -> None:
    errors.extend(validate(schemas[schema_file], value, schemas, schema_file, label))


def find_cycle(nodes: list[str], edges: list[dict[str, str]]) -> list[str] | None:
    adj: dict[str, list[str]] = {n: [] for n in nodes}
    for e in edges:
        adj.setdefault(e["from"], []).append(e["to"])
    state: dict[str, int] = {}
    stack: list[str] = []

    def dfs(n: str) -> list[str] | None:
        if state.get(n) == 2:
            return None
        if state.get(n) == 1:
            return [*stack[stack.index(n) :], n]
        state[n] = 1
        stack.append(n)
        for m in adj.get(n, []):
            if (c := dfs(m)) is not None:
                return c
        stack.pop()
        state[n] = 2
        return None

    for n in nodes:
        if (c := dfs(n)) is not None:
            return c
    return None


def scan_secrets(d: Path, snap: str) -> None:
    for p in sorted(d.rglob("*")):
        if p.is_file() and p.suffix in {".json", ".jsonl", ".md", ".txt", ".env"}:
            if SECRET_RE.search(p.read_text(encoding="utf-8", errors="ignore")):
                err(f"{snap}: {p.name} 里有疑似真实凭证。★ 固件请用 REPLACE_ME_xxx 占位。")


def main() -> None:
    snapshots = sorted(p.name for p in GOLDEN.iterdir() if p.is_dir())
    if len(snapshots) < 3:
        err(f"只有 {len(snapshots)} 个 golden-state 快照。★ 第 0 周要求 ≥3 个。")

    file_count = 0

    for snap in snapshots:
        d = GOLDEN / snap
        c = d / ".codentum"

        if not (d / "NOTE.md").exists():
            err(f"{snap}: 缺 NOTE.md。★ 没有 NOTE 的快照，半年后没人知道那个负数是 bug 还是故意的。")

        for fname, sf in (("graph.json", "graph.schema.json"), ("budget.json", "budget.schema.json")):
            p = c / fname
            if not p.exists():
                err(f"{snap}: 缺 .codentum/{fname}")
                continue
            check(f"{snap}/{fname}", sf, read_json(p))
            file_count += 1

        for p in list_json(c / "packets"):
            check(f"{snap}/packets/{p.name}", "workpacket.schema.json", read_json(p))
            file_count += 1
        for p in list_json(c / "evidence"):
            check(f"{snap}/evidence/{p.name}", "evidence.schema.json", read_json(p))
            file_count += 1

        dj = c / "decisions.jsonl"
        if dj.exists():
            for i, line in enumerate(dj.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    err(f"{snap}/decisions.jsonl:{i}: 不是合法 JSON")
                    continue
                check(f"{snap}/decisions.jsonl:{i}", "decision.schema.json", obj)
            file_count += 1

        # ── ★ 八项交叉检查 ──────────────────────────────────
        packets = [read_json(p) for p in list_json(c / "packets")]
        running = [p for p in packets if p.get("state") == "running"]

        # ① I1 单写者
        for i, a in enumerate(running):
            for b in running[i + 1 :]:
                for pa in a.get("ownsPaths", []):
                    for pb in b.get("ownsPaths", []):
                        if pa.startswith(pb) or pb.startswith(pa):
                            err(f'{snap}: ★ I1 违反 —— {a["id"]} 的 "{pa}" 与 {b["id"]} 的 "{pb}" 路径相交')

        # ② 验收制衡
        for pkt in packets:
            if pkt.get("acceptance", {}).get("authoredBy") == pkt.get("role"):
                err(f'{snap}/{pkt["id"]}: ★ acceptance.authoredBy === role ({pkt["role"]}) —— 自己给自己定验收')

        gp = c / "graph.json"
        if gp.exists():
            g = read_json(gp)
            run_ids = {p["id"] for p in running}

            # ③ 锁持有者必须是 running
            for lock in g.get("ownership", {}).get("locks", []):
                if lock["heldBy"] not in run_ids:
                    err(
                        f'{snap}: 锁 "{lock["pathPrefix"]}" 由 {lock["heldBy"]} 持有，但它不是 running。'
                        f" ★ 卡住即释放锁 —— 否则一个卡住的任务会锁死整条流水线。"
                    )

            # ④ 依赖图无环
            dep = g.get("dependency", {})
            if (cyc := find_cycle(dep.get("nodes", []), dep.get("edges", []))) is not None:
                err(f'{snap}: ★ 依赖图成环 —— {" → ".join(cyc)}。依赖图必须是 DAG。')

            # ⑤ 图与文件一致
            node_ids = set(dep.get("nodes", []))
            file_ids = {p["id"] for p in packets}
            for pid in sorted(node_ids - file_ids):
                err(f"{snap}: graph.json 有节点 {pid}，但 packets/ 里没有对应文件")
            for pid in sorted(file_ids - node_ids):
                err(f"{snap}: packets/ 有 {pid}，但 graph.json 的 nodes 里没有它")

        # ⑥ I6 审计哈希链
        ev_by_packet: dict[str, list[dict[str, Any]]] = {}
        for p in list_json(c / "evidence"):
            e = read_json(p)
            ev_by_packet.setdefault(e["packetId"], []).append(e)
        for pid, lst in sorted(ev_by_packet.items()):
            lst.sort(key=lambda e: str(e.get("at", "")))
            for i, e in enumerate(lst):
                expected = CHAIN_HEAD if i == 0 else lst[i - 1]["digest"]
                if e.get("prevDigest") != expected:
                    who = "链首哨兵" if i == 0 else f'{lst[i-1]["ref"]}.digest'
                    err(
                        f'{snap}/{pid}: ★ 审计链断裂 —— {e["ref"]}.prevDigest 应为 {who}，'
                        f'实际 "{e.get("prevDigest")}"。断链即篡改，这条不能只写在文档里。'
                    )
            if len({e["digest"] for e in lst}) != len(lst):
                err(f"{snap}/{pid}: 证据 digest 有重复 —— 哈希链要求唯一")

        # ⑦ 证据引用有效
        ev_refs = {read_json(p)["ref"] for p in list_json(c / "evidence")}
        for pkt in packets:
            for r in pkt.get("evidence", []):
                if r not in ev_refs:
                    err(f'{snap}/{pkt["id"]}: 引用了不存在的证据 "{r}"')

        # ⑧ 无真实凭证
        scan_secrets(c, snap)

    if errors:
        print(f"\n✗ validate-fixtures：{len(errors)} 处问题\n", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        print("", file=sys.stderr)
        raise SystemExit(1)

    print(
        f"✓ {len(snapshots)} 个快照 / {file_count} 个文件通过 schema 校验，并通过八项交叉检查"
        f"（I1 路径不相交 · 验收作者≠执行角色 · 锁持有者为 running · 依赖图无环 · "
        f"图与文件一致 · I6 审计链首尾相接 · 证据引用有效 · 无凭证）"
    )


if __name__ == "__main__":
    main()
