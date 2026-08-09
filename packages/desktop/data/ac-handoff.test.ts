import { execFileSync } from 'node:child_process'
import { mkdtemp } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

import { ProjectStateSource } from './index'

/**
 * A → C 交接的真测试。
 *
 * `.codentum/` 是 A（control-plane）和 C（desktop）之间唯一的接口：A 写，C 读。
 * 双方各有一套校验 —— A 那边是 Pydantic 模型，C 这边是手写的 `isGraphFile`
 * 和 `checkGraphPacketCoherence`。两套东西没有共同的生成源，所以完全可能
 * 各自「自测通过」却互不兼容。
 *
 * 这里不抄任何一方的判定逻辑：让 A 真的跑一遍、真的落盘，再让 C 真的加载。
 * 只有这样才测得到接缝本身。
 */

const PYTHON_DRIVER = `
import json, subprocess, sys
from pathlib import Path

from codentum_contracts.state import PacketId, WorkPacket, dump_state
from codentum_control_plane.budget import BudgetTracker
from codentum_control_plane.reconcile import ReconcileLoop

repo = Path(sys.argv[1])
for cmd in (["git", "init", "-q"],
            ["git", "config", "user.email", "ac@codentum.local"],
            ["git", "config", "user.name", "codentum-ac"]):
    subprocess.run(cmd, cwd=repo, check=True, capture_output=True)

def make(pid, deps=()):
    return WorkPacket(
        id=PacketId(pid), kind="impl", state="pending", role="coder",
        ownsPaths=(f"src/{pid}/",), readsPaths=("tests/",),
        deps=tuple(PacketId(d) for d in deps),
        acceptance={"kind": "test", "predicate": "pytest", "authoredBy": "qa"},
        budget={"currency": "CNY", "limitCny": 5.0, "spentCny": 0.0,
                "degradationChain": ("drop_semantic",)},
        attempts=0, evidence=(),
        provenance={"createdBy": "planner", "createdAt": "2026-08-05T00:00:00Z"},
    )

state_dir = repo / ".codentum"
(state_dir / "packets").mkdir(parents=True, exist_ok=True)
for packet in (make("wp-ac0001"), make("wp-ac0002", deps=("wp-ac0001",))):
    (state_dir / "packets" / f"{packet.id}.json").write_text(
        json.dumps(dump_state(packet), indent=2, ensure_ascii=False) + "\\n",
        encoding="utf-8")

# 真实运行必然配着预算追踪器 —— 预算就是系统的护栏之一。
# 不配的话 A 写不出 budget.json，这里就测不到完整形状。
loop = ReconcileLoop(state_dir=str(state_dir),
                     budget_tracker=BudgetTracker(limit_cny=20.0))
loop.load_state()
loop.run_until_stable(max_ticks=20)
loop.save_state()
`

function runControlPlane(repo: string): boolean {
  const root = join(__dirname, '..', '..', '..')
  const pythonPath = [
    join(root, 'packages', 'contracts', 'python'),
    join(root, 'packages', 'control-plane')
  ].join(process.platform === 'win32' ? ';' : ':')

  for (const python of ['python', 'python3']) {
    try {
      execFileSync(python, ['-c', PYTHON_DRIVER, repo], {
        env: { ...process.env, PYTHONPATH: pythonPath, PYTHONIOENCODING: 'utf-8' },
        stdio: 'pipe'
      })
      return true
    } catch (error) {
      const code = (error as NodeJS.ErrnoException).code
      if (code === 'ENOENT') continue // 换下一个解释器名
      throw error // 真跑挂了 —— 不能当成"环境缺失"悄悄跳过
    }
  }
  return false
}

describe('A → C handoff', () => {
  it('loads a state directory written by the real control plane without incoherence', async () => {
    const repo = await mkdtemp(join(tmpdir(), 'codentum-ac-'))
    if (!runControlPlane(repo)) {
      // 找不到 Python 解释器时跳过，但绝不把"跑失败"也算进来（见上面的 throw）
      return
    }

    const source = await ProjectStateSource.create(repo)
    const snapshot = await source.read()
    source.close()

    const warnings = snapshot.warnings.join('\n')
    // ★ 这条曾经是红的：A 写的 graph.dependency.nodes 恒为空，
    //   C 的 checkGraphPacketCoherence 把每一次全新流程都判成不连贯。
    //   桌面端不会崩，只会安静地显示错的东西 —— 所以必须有测试盯着。
    expect(warnings).not.toContain('[partial-write]')
    expect(warnings).not.toContain('[bad-json]')
    // ★ 同样曾经是红的：A 只写 graph.json 和 packets/，
    //   而 `.codentum/` 的形状还包括 decisions.jsonl · evidence/ · knowledge/。
    //   缺一个，C 就把整份快照判为 incoherent。
    expect(warnings).not.toContain('[missing]')
    expect(warnings).not.toContain('[schema]')
    expect(snapshot.packets.map((packet) => packet.id).sort()).toEqual([
      'wp-ac0001',
      'wp-ac0002'
    ])
  })
})
