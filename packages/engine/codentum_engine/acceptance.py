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

from codentum_contracts.state import EvidenceRef, WorkPacket
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
                evidence_refs=_as_refs(acceptance_evidence(packet.evidence)),
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

        # ── 第五层：验收测试本身不能是空的 ────────────────
        vacuous = _vacuity_check(workspace, command)
        if vacuous is not None:
            return GateVerdict(passed=False, gate_id="acceptance", detail=vacuous)

        return GateVerdict(
            passed=True,
            gate_id="acceptance",
            detail=f"验收通过：`{predicate}` 退出码 0（且实现被移走后确实变红）\n{tail[-400:]}",
            evidence_refs=_as_refs(acceptance_evidence(packet.evidence)),
        )

    return gate


def _vacuity_check(workspace: Path, command: list[str]) -> str | None:
    """★ 把实现移走，再跑一遍验收 —— **它必须变红**。

    这是本项目那句口号的可执行版本：

        「如果它坏了，哪条测试会变红？」

    2026-08-12 实测：模型写出了正确的实现，测试文件却只有一句 `assert True`。
    验收谓词 `pytest workspace -q` 返回 1 passed、退出码 0，packet 被 accepted ——
    **验收标准达到了，而验收标准本身什么都没验证。**

    这是那条线的第五层：

      08-09  拿控制面自己的簿记当证据
      08-10  门禁层同一个洞
      08-11  说完成了但一个文件没改
      08-12  改了文件，但没达到验收标准
      **本层：达到了验收标准，但验收标准是空的**

    ★ 为什么用「移走实现」而不是查测试内容：
      查内容是模式匹配 —— `assert True` 能查出来，`import x; assert True`
      就查不出来，而且模型总能绕过。移走实现是**因果检验**：
      测试若真的在验证实现，实现没了它必然红。这一条绕不过去。

    ★ 局限（必须说清楚）：只在实现与测试**分文件**时有效。
      两者写在同一个文件里时这一层不生效 —— 那需要 QA 独立出题才能解决，
      而 QA 目前还没有独立的 packet。**这个缺口记在案，不假装它不存在。**

    返回 None 表示通过；返回字符串表示「验收是空的」及其说明。
    """

    impl_files = [
        path
        for path in sorted(workspace.rglob("*.py"))
        if ".codentum" not in path.parts
        and ".git" not in path.parts
        and not path.name.startswith("test_")
        and not path.name.endswith("_test.py")
        and "conftest" not in path.name
    ]
    if not impl_files:
        # 没有可移走的实现文件 —— 这一层不适用，交给别的判据
        return None

    moved: list[tuple[Path, Path]] = []
    try:
        for path in impl_files:
            hidden = path.with_suffix(path.suffix + ".vacuity-check")
            path.rename(hidden)
            moved.append((path, hidden))

        still_green = False
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
            still_green = proc.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            # ★ 检不了就别拦 —— 但也别谎称检过了。
            return None

        if not still_green:
            # 实现被移走后确实变红 —— 这些测试真的在验证实现
            return None

        names = "、".join(path.name for path in impl_files)
        return (
            "验收未通过：**验收测试是空的**。\n"
            f"把实现（{names}）移走之后，验收谓词仍然退出码 0 —— "
            "说明这些测试根本没有验证实现。\n\n"
            "★ 判据是「如果它坏了，哪条测试会变红」。现在的答案是：一条都不会。"
        )
    finally:
        # ★ 无论如何都要还原。这里失败会把工作区留在残破状态，
        #   而那比验收判错严重得多。
        for original, hidden in moved:
            if hidden.exists():
                hidden.rename(original)


def _as_refs(refs: tuple[str, ...]) -> tuple[EvidenceRef, ...]:
    """`acceptance_evidence` 返回 `tuple[str, ...]`，而 `GateVerdict` 要
    `tuple[EvidenceRef, ...]` —— EvidenceRef 是 NewType，运行时同一个 str，
    但类型层面要显式转，否则 mypy 会（正确地）拦下来。"""

    return tuple(EvidenceRef(ref) for ref in refs)


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
