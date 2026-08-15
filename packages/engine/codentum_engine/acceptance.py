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
import ast
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from codentum_contracts.state import EvidenceRef, WorkPacket
from codentum_control_plane.evidence import acceptance_evidence, worker_failure_markers
from codentum_control_plane.gates import GateVerdict

__all__ = [
    "ACCEPTANCE_TIMEOUT_SECONDS",
    "build_executing_acceptance_gate",
    "split_command",
    "CompositionResult",
    "composition_check",
    "discover_modules",
    "vacuity_check",
]

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

        command = split_command(predicate)
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

        notes: list[str] = []

        # ── 第五层：验收测试本身不能是空的 ────────────────
        vacuous = vacuity_check(workspace, command)
        if vacuous is not None:
            return GateVerdict(passed=False, gate_id="acceptance", detail=vacuous)

        # ── 第六层：集成判据必须真的覆盖每个模块 ──────────
        #
        # ★ 只对集成 packet 生效。对单模块 packet 谈「组合」没有意义，
        #   而且 vacuity_check 已经守住了那一层。
        if packet.role == "integrator":
            modules = discover_modules(workspace)
            if len(modules) >= 2:
                composition = composition_check(workspace, command, modules=modules)
                if composition.uncovered is not None:
                    return GateVerdict(
                        passed=False, gate_id="acceptance", detail=composition.uncovered
                    )
                if composition.inconclusive is not None:
                    # ★ 没检成不拦人（检不了就别拦），但**必须写进 detail**。
                    #   否则负载高的环境里这一层会静默失效，而报告上只有
                    #   一句「验收通过」—— 那就是谎称检过了。
                    notes.append(composition.inconclusive)

        return GateVerdict(
            passed=True,
            gate_id="acceptance",
            detail=(
                f"验收通过：`{predicate}` 退出码 0（且实现被移走后确实变红）"
                + "".join(f"\n⚠️ {note}" for note in notes)
                + f"\n{tail[-400:]}"
            ),
            evidence_refs=_as_refs(acceptance_evidence(packet.evidence)),
        )

    return gate


def vacuity_check(workspace: Path, command: list[str]) -> str | None:
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
        # ★ 把**谓词作用域内实际存在的测试文件**列出来。
        #
        #   2026-08-12 实测：模型收到「测试是空的」之后确实去写了真测试 ——
        #   但写到了 `tests/` 下，而谓词只看 `workspace/`。于是空测试原地不动、
        #   它却以为自己修好了，剩下三轮全耗在 `git diff` 上。
        #
        #   只说「是空的」不够，得说清**哪个文件是空的、该改哪个**。
        test_files = [
            path.relative_to(workspace).as_posix()
            for path in sorted(workspace.rglob("test_*.py"))
            if ".codentum" not in path.parts and ".git" not in path.parts
        ]
        scope = "、".join(test_files) if test_files else "（作用域内没有任何测试文件）"
        return (
            "验收未通过：**验收测试是空的**。\n"
            f"把实现（{names}）移走之后，验收谓词仍然退出码 0 —— "
            "说明这些测试根本没有验证实现。\n\n"
            f"★ 验收谓词只会看这些文件：{scope}\n"
            "**请直接改写上面列出的文件**，不要在别的目录新建测试 —— 谓词看不到那里。\n\n"
            "★ 判据是「如果它坏了，哪条测试会变红」。现在的答案是：一条都不会。"
        )
    finally:
        # ★ 无论如何都要还原。这里失败会把工作区留在残破状态，
        #   而那比验收判错严重得多。
        for original, hidden in moved:
            if hidden.exists():
                hidden.rename(original)


class _BodyStubber(ast.NodeTransformer):
    """把函数体掏空成 `return None`，**保留签名、装饰器、类结构与模块级语句**。"""

    def _stub(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.AST:
        self.generic_visit(node)   # 先处理嵌套函数
        node.body = [ast.Return(value=ast.Constant(value=None))]
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        return self._stub(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        return self._stub(node)


def _stub_source(source: str) -> str | None:
    """签名保真地掏空一份源码。语法错误时返回 None（不该由这一层去报）。"""

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    tree = _BodyStubber().visit(tree)
    ast.fix_missing_locations(tree)
    return ast.unparse(tree)


def discover_modules(workspace: Path) -> list[str]:
    """按 Planner 的约定找出模块 —— `workspace/<module>/src/`。

    ★ 从**工作区的实际状态**推导，而不是从 packet 上的声明读。
      声明会与现实漂移（改了目录忘了改声明），而目录不会 ——
      这一层要判的正是「实际存在的模块有没有被集成测试覆盖」。
    """

    modules: list[str] = []
    for src in sorted(workspace.rglob("src")):
        if not src.is_dir() or src.parent == workspace:
            continue
        rel = src.parent.relative_to(workspace).as_posix()
        if ".codentum" in rel or ".git" in rel or "/src/" in f"/{rel}/":
            continue
        modules.append(rel)
    return modules


def _is_test_file(path: Path) -> bool:
    return (
        "tests" in path.parts
        or path.name.startswith("test_")
        or path.name.endswith("_test.py")
    )


def _module_impl_files(workspace: Path, module: str) -> list[Path]:
    root = workspace / module
    if not root.is_dir():
        return []
    return [
        path
        for path in sorted(root.rglob("*.py"))
        if not _is_test_file(path) and "conftest" not in path.name
    ]


def _module_test_files(workspace: Path, module: str) -> list[Path]:
    root = workspace / module
    if not root.is_dir():
        return []
    return [
        path
        for path in sorted(root.rglob("*.py"))
        if _is_test_file(path) and "conftest" not in path.name
    ]


@dataclass(frozen=True, slots=True)
class CompositionResult:
    """三种结局必须可区分。

    ★ 最初这个函数只返回 `str | None`，于是「全部覆盖」与「超时没检成」
      **返回同一个值**。门禁随后写的是「验收通过」——
      而那正是本项目一路在拆的那句话：**谎称检过了**。

    ★ 「没检成」不拦人是对的（检不了就别拦），但它必须**说出来**：
      否则一个负载高的环境里，这一层会静默失效，而报告上看不出区别。
    """

    uncovered: str | None = None
    """非 None = 应当拦下，内容是给模型看的说明。"""

    inconclusive: str | None = None
    """非 None = 这一层没能执行，内容是原因。**不拦人，但必须写进 detail。**"""


def composition_check(
    workspace: Path, command: list[str], *, modules: Sequence[str]
) -> CompositionResult:
    """★ 把**某一个模块**的实现桩化，集成谓词必须变红 —— 对每个模块都如此。

    ════════════════════════════════════════════════════════════
     这一层防的是 `vacuity_check` 防不住的那个
    ════════════════════════════════════════════════════════════

    `vacuity_check` 能抓「测试是空的」。它抓不到一个更隐蔽的东西：

        **集成测试只是把各模块自己的测试又跑了一遍。**

    多 Agent 并行开发最经典的失败就是「各段都对、合起来不通」，
    而集成判据恰恰是最容易造假的一个 —— 写它的人**没有动力让它变红**。
    全量跑一遍所有测试，退出码 0，看起来无懈可击；
    但它完全没有检验模块之间的接口是不是对得上。

    ★ 算子：把模块 A 的实现掏空，集成谓词**必须**变红。
      如果它照样通过 —— 集成测试根本没测到 A 的参与。

    ════════════════════════════════════════════════════════════
     为什么是「掏空函数体」而不是「删掉文件」
    ════════════════════════════════════════════════════════════

    删文件会得到 ImportError。测试确实变红了，但那是**红对了、理由错了**：
    它只证明「A 的文件存在」，证明不了「集成测试覆盖了 A 的行为」。
    一个只写了 `import a` 的集成测试，在删文件时也会红。

    所以桩必须是**合法可导入**的：同名函数、同参数、同装饰器，
    模块级常量与类结构原样保留 —— 只有函数体变成 `return None`。
    这样导入照常成功，**只有真的调用了 A 的行为的测试才会红**。

    ★ 与 `vacuity_check` 的分工：
      vacuity 问「测试有没有在验证实现」（单点）；
      这一层问「集成判据有没有覆盖它声称覆盖的每一个模块」（组合）。
      前者是判据的**存在性**，后者是判据的**作用域真实性**。

    ════════════════════════════════════════════════════════════
     ★ 为什么必须同时把该模块**自己的测试**藏起来
    ════════════════════════════════════════════════════════════

    不藏的话这个算子自己就是空的：

        集成谓词若是 `pytest workspace -q`（跑全部测试），
        桩化 A 一定会让 **A 自己的单元测试**变红 ——
        于是每个模块都显示「已覆盖」，**这个检查永远返回通过**。

    那正是它自己要防的那种「零输入的绿灯」，只不过发生在检查器身上。

    藏掉 A 的自测之后，剩下还会红的必然来自 A 之外 —— 也就是真正跨模块的用例。
    这一步不是优化，是**结论成立的前提**。

    ★ 局限（说清楚，不假装没有）：
      · 只对「模块 = 目录」的拆分方式有效，而 Planner 正是这么分的
      · 桩化后若因 TypeError 而红，仍算作「覆盖到了」—— 那确实说明
        集成测试用到了 A 的返回值。但它不能区分「用到了」与「验证了」。

    返回 None 表示每个模块都被覆盖；返回字符串列出没被覆盖的模块。
    """

    uncovered: list[str] = []
    skipped: list[str] = []

    for module in modules:
        impl_files = _module_impl_files(workspace, module)
        if not impl_files:
            skipped.append(module)
            continue

        saved: list[tuple[Path, str]] = []
        hidden: list[tuple[Path, Path]] = []
        try:
            # ★ 把本模块**自己的测试**藏起来 —— 这一步是这个算子成立的前提。
            #
            #   不藏的话：集成谓词若是 `pytest workspace -q`（跑全部测试），
            #   桩化 A 一定会让 A 自己的单元测试变红 —— 于是每个模块都显示
            #   「已覆盖」，**这个检查永远返回通过**。
            #   那正是它自己要防的那种空判据。
            #
            #   藏掉之后，剩下还会红的必然来自 A 之外的测试 ——
            #   也就是真正跨模块的那些。
            for path in _module_test_files(workspace, module):
                away = path.with_suffix(path.suffix + ".composition-check")
                path.rename(away)
                hidden.append((path, away))

            stubbed_any = False
            for path in impl_files:
                original = path.read_text(encoding="utf-8")
                stub = _stub_source(original)
                if stub is None:
                    continue
                saved.append((path, original))
                path.write_text(stub, encoding="utf-8")
                stubbed_any = True
            if not stubbed_any:
                skipped.append(module)
                continue

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
            except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
                # ★ 检不了就别拦 —— 但也别谎称检过了。**把原因带出去。**
                return CompositionResult(
                    inconclusive=(
                        f"组合检验未能执行（{type(exc).__name__}）：模块 {module} 的"
                        f"谓词没跑完，因此**这一层没有检过**。"
                    )
                )

            if still_green:
                uncovered.append(module)
        finally:
            # ★ 还原必须在 finally，而且是**逐个模块**还原，不是最后统一还原 ——
            #   中途抛异常时，已经桩化的模块必须先复原再往下走，
            #   否则后一个模块的检验会在一个残破的工作区里进行，结论毫无意义。
            for path, original in saved:
                path.write_text(original, encoding="utf-8")
            for original_path, away in hidden:
                if away.exists():
                    away.rename(original_path)

    if not uncovered:
        return CompositionResult()

    names = "、".join(uncovered)
    note = ""
    if skipped:
        note = "\n（跳过了 " + "、".join(skipped) + " —— 没有找到可桩化的实现文件）"
    return CompositionResult(uncovered=(
        f"集成验收未通过：**集成测试没有覆盖 {names}**。\n"
        f"把 {names} 的实现全部掏空（保留签名，只让函数返回 None）之后，"
        "集成谓词**仍然退出码 0**。\n\n"
        "★ 这说明集成测试很可能只是把各模块自己的测试又跑了一遍，"
        "而没有真的检验模块之间的接口。\n"
        "「各段都对、合起来不通」正是这样漏过去的。\n\n"
        f"**请补充真正跨模块的用例**：调用 {names} 的公开接口，"
        "并断言它的返回值参与了最终结果。" + note
    ))


def _as_refs(refs: tuple[str, ...]) -> tuple[EvidenceRef, ...]:
    """`acceptance_evidence` 返回 `tuple[str, ...]`，而 `GateVerdict` 要
    `tuple[EvidenceRef, ...]` —— EvidenceRef 是 NewType，运行时同一个 str，
    但类型层面要显式转，否则 mypy 会（正确地）拦下来。"""

    return tuple(EvidenceRef(ref) for ref in refs)


def split_command(predicate: str) -> list[str]:
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
