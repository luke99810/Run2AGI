"""真正**执行**验收谓词的门禁 —— 把「有证据」升级成「验收标准确实过了」。

════════════════════════════════════════════════════════════════
 ★ 在这之前，验收从来没有被执行过
════════════════════════════════════════════════════════════════

`gates/builtin.py::acceptance_gate` 的注释写得很清楚：

    P0 简化：检查 evidence 非空且 acceptance.authoredBy != packet.role。
    完整版：实际运行验收测试并检查结果。

**「完整版」一直没写。** 于是 2026-08-12 那次真机跑通里，模型只写了规格
要求的两个文件中的一个（自己在第 5 轮承认了没写测试），packet 仍然
被判 `accepted` —— 因为它确实产生了一条真实证据。

> 「写了 1 个文件」与「完成了需求」之间的差距，判据判不出来。

这正是 §二十二 那件事的下一层：
- 08-09 修的是「拿控制面自己的簿记当证据」
- 08-10 修的是「门禁层同一个洞」
- 08-11 修的是「说完成了但一个文件没改」
- **这一层是：改了文件，但没达到验收标准**

前三层都能靠「看有没有 X」解决。这一层不能 —— 必须**真的把验收谓词跑一遍**。

════════════════════════════════════════════════════════════════
 ★ 为什么放在 engine 而不是 control-plane
════════════════════════════════════════════════════════════════

执行验收要在 **worker 的工作区**里跑命令，而控制平面的承诺是
「确定性代码，零 LLM，不派生子进程」。把 subprocess 塞进去会稀释那个承诺。

装配点本来就负责「装什么门禁」—— 它把这个会执行命令的门禁注册进
`GateRunner`，控制平面仍然只看到一个 `GateFn`。
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
from pathlib import Path

from codentum_contracts.state import WorkPacket
from codentum_control_plane.evidence import acceptance_evidence, worker_failure_markers
from codentum_control_plane.gates import GateVerdict

__all__ = ["ACCEPTANCE_TIMEOUT_SECONDS", "build_executing_acceptance_gate"]

logger = logging.getLogger(__name__)

ACCEPTANCE_TIMEOUT_SECONDS = 180.0


def build_executing_acceptance_gate(workers_root: Path | str):  # type: ignore[no-untyped-def]
    """造一个**会真的运行验收谓词**的门禁。

    `workers_root` 是 worker 工作区的父目录（`ReconcileLoop._build_spawn_request`
    里那个 `codentum-workers/`）。
    """

    root = Path(workers_root)

    def gate(packet: WorkPacket, **_ctx: object) -> GateVerdict:
        # ── 前三层判据仍然全部保留 ──────────────────────
        failed = worker_failure_markers(packet.evidence)
        if failed:
            return GateVerdict(
                passed=False,
                gate_id="acceptance",
                detail=f"验收未通过：worker 明确失败过（{', '.join(failed)}）。",
            )

        if not acceptance_evidence(packet.evidence):
            return GateVerdict(
                passed=False,
                gate_id="acceptance",
                detail="验收未通过：缺少验收证据（sys: 簿记不算）。",
            )

        if packet.acceptance.authoredBy == packet.role:
            return GateVerdict(
                passed=False,
                gate_id="acceptance",
                detail=f"验收未通过：{packet.role} 不能给自己的 packet 定验收标准（I2）。",
            )

        # ── 第四层：真的把谓词跑一遍 ────────────────────
        kind = packet.acceptance.kind
        predicate = packet.acceptance.predicate.strip()

        if kind != "test":
            # ★ 非 test 类验收（manual / metric / schema）这里跑不了，
            #   **如实说明**而不是默默放行。放行本身仍然发生（保持既有行为），
            #   但 detail 里必须写清楚"这一条没有被执行过" ——
            #   否则下游看到 passed=True 会以为验收真的过了。
            return GateVerdict(
                passed=True,
                gate_id="acceptance",
                detail=(
                    f"验收证据齐全，但 kind={kind} 的谓词**未被执行**"
                    f"（只有 kind=test 能自动判定）：{predicate[:120]}"
                ),
                evidence_refs=acceptance_evidence(packet.evidence),
            )

        workspace = _latest_workspace(root, packet)
        if workspace is None:
            return GateVerdict(
                passed=False,
                gate_id="acceptance",
                detail=f"验收未通过：找不到 {packet.id} 的工作区，无法运行验收谓词。",
            )

        command = _split_command(predicate)
        if not command:
            return GateVerdict(
                passed=False,
                gate_id="acceptance",
                detail=f"验收未通过：谓词不是可执行命令：{predicate!r}",
            )

        try:
            proc = subprocess.run(  # noqa: S603 - argv 明确，shell=False
                command,
                cwd=workspace,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=ACCEPTANCE_TIMEOUT_SECONDS,
                stdin=subprocess.DEVNULL,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            return GateVerdict(
                passed=False,
                gate_id="acceptance",
                detail=f"验收未通过：谓词超时（>{ACCEPTANCE_TIMEOUT_SECONDS:.0f}s）：{predicate}",
            )
        except FileNotFoundError:
            return GateVerdict(
                passed=False,
                gate_id="acceptance",
                detail=f"验收未通过：找不到可执行文件 {command[0]!r}",
            )

        tail = (proc.stdout + proc.stderr).strip()[-1200:]
        if proc.returncode != 0:
            return GateVerdict(
                passed=False,
                gate_id="acceptance",
                detail=f"验收未通过：`{predicate}` 退出码 {proc.returncode}\n{tail}",
            )

        return GateVerdict(
            passed=True,
            gate_id="acceptance",
            detail=f"验收通过：`{predicate}` 退出码 0\n{tail[-400:]}",
            evidence_refs=acceptance_evidence(packet.evidence),
        )

    return gate


def _split_command(predicate: str) -> list[str]:
    r"""把谓词切成 argv。**Windows 上必须 posix=False。**

    ★ `shlex.split(s)` 默认 posix=True，会把反斜杠当转义符 ——
      `D:\Anaconda\python.exe` 被切成 `D:Anacondapython.exe`，
      于是谓词永远找不到可执行文件、验收永远不通过，
      而现象看起来像「模型没干活」。

      这是「判据在一个平台上有效、在另一个平台上失效且不报错」的又一次 ——
      本项目已经踩过三次（EvidenceRef 分隔符、流编码、touched_paths 归一化）。
    """

    if os.name == "nt":
        return [token.strip('"') for token in shlex.split(predicate, posix=False)]
    return shlex.split(predicate)


def _latest_workspace(root: Path, packet: WorkPacket) -> Path | None:
    """找到这个 packet 最近一次尝试的工作区。

    ★ 按 attempt 倒着找而不是取最大编号目录：重试时 attempt 会增长，
      而验收要看的是**刚跑完的那一次**。
    """

    for attempt in range(packet.attempts, 0, -1):
        candidate = root / str(packet.id) / f"attempt-{attempt}"
        if candidate.is_dir():
            return candidate
    return None
