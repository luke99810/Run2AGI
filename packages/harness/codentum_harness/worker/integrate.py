"""把 worker 在隔离 worktree 里的产出合回主项目。

════════════════════════════════════════════════════════════════
 ★ 缺了这一段，「accepted」是一句空话
════════════════════════════════════════════════════════════════

worker 在独立 worktree 里写代码，证据会被镜像回 `.codentum/evidence/`，
但**代码改动一直留在 worktree 里**。也就是说：

    packet 被判 accepted → 使用者打开项目 → **什么都没有**

「验收通过」必须意味着**东西真的进了项目**，否则它只是一句状态字符串。

════════════════════════════════════════════════════════════════
 ★ 三条拒绝的理由，每一条都是安全判据
════════════════════════════════════════════════════════════════

**一、项目工作区不干净就拒绝。**
项目根目录是**使用者的工作目录**。在那里做 merge 可能冲掉他没提交的改动 ——
这与 `ensure_project_initialized` 拒绝碰有历史的仓库是同一条原则：
**别人的东西，不经允许不动。**

**二、改动越出 ownsPaths 就拒绝。**
I1（单写者）靠路径独占成立。worker 的工具边界只拦「越出工作区」，
不拦「越出自己拥有的路径」—— 于是一个 packet 可以写到别人的地盘上。
在 worktree 里那还只是本地文件，**一旦合进主项目就真的破坏了 I1**。

★ 拒绝而不是「悄悄只合法的那部分」：静默丢弃会让 worker 以为写成功了，
  而产出对不上，排查时完全看不出发生过什么。

**三、合并冲突就中止并还原。**
I1 成立时不该有冲突；真出现了说明前面某条不变量已经破了。
这时候留一个冲突中的仓库给使用者，比不合入糟得多。
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from shutil import which

__all__ = ["IntegrationResult", "integrate_worker_result"]

_BOOKKEEPING_PREFIX = ".codentum/"
"""系统自己写进工作区的簿记 —— 不是 worker 的产出，不参与合入。

★ 与 `reconcile/loop.py::BOOKKEEPING_PATH_PREFIX` 同一个约定。
  那边用它判「干没干活」，这边用它判「合什么」——
  两处用途不同但依据相同，改一处要想到另一处。
"""


@dataclass(frozen=True, slots=True)
class IntegrationResult:
    """一次合入的结果。

    ★ `merged=True` 且 `commit is None` 是**合法且常见**的：
      评审类 packet 本来就可能一个文件都不改。
      把「没有改动」和「合入失败」混成一个布尔，会让前者被当成故障。
    """

    merged: bool
    detail: str
    commit: str | None = None


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    exe = which("git")
    if exe is None:
        raise RuntimeError("git executable not found")
    return subprocess.run(  # noqa: S603 - 固定可执行文件与参数表，无 shell
        [exe, "-C", str(cwd), *args],
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _changed_paths(workspace: Path) -> list[str]:
    # ★ 必须 -uall：默认的 --porcelain 会把未跟踪的**目录**折叠成一条
    #   （`workspace/`），而 ownsPaths 是按文件路径判的 ——
    #   折叠之后一个合法的 `workspace/alpha/m.py` 会以 `workspace/` 的形式
    #   出现，被误判成越界。这个缺陷是被 I1 那条测试当场抓住的。
    # ★ core.quotepath=false：git 默认把非 ASCII 文件名转义成八进制
    #   （`我的草稿.txt` → `/346/210/221/...`）。而这些路径会**原样出现在
    #   给使用者看的拒绝消息里** —— 转义之后那条消息等于没说。
    #   本仓库已经栽过两次编码的坑（EvidenceRef 分隔符、流编码），这是第三次。
    out = _git(workspace, "-c", "core.quotepath=false", "status", "--porcelain", "-uall").stdout
    paths: list[str] = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        raw = line[3:].strip().strip('"')
        # 重命名形如 `old -> new`，取新路径
        if " -> " in raw:
            raw = raw.split(" -> ", 1)[1]
        normalized = raw.replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        if normalized.startswith(_BOOKKEEPING_PREFIX):
            continue
        paths.append(normalized)
    return paths


def _outside_owned(paths: list[str], owns_paths: tuple[str, ...]) -> list[str]:
    owned = [p.replace("\\", "/").strip("/") for p in owns_paths if p.strip()]
    if not owned:
        # ★ 没有声明写权限却产生了改动 —— 全部算越界。
        #   准入规则 check_owns_paths 已经拦过空 ownsPaths，
        #   走到这里说明状态被人绕过了，宁可拒绝。
        return list(paths)
    outside: list[str] = []
    for path in paths:
        if not any(path == own or path.startswith(f"{own}/") for own in owned):
            outside.append(path)
    return outside


def integrate_worker_result(
    repo_root: Path | str,
    workspace: Path | str,
    *,
    packet_id: str,
    owns_paths: tuple[str, ...],
) -> IntegrationResult:
    """把 worktree 里的产出提交并合进项目当前分支。"""

    repo = Path(repo_root).resolve()
    ws = Path(workspace).resolve()

    if not ws.is_dir():
        return IntegrationResult(False, f"找不到 {packet_id} 的工作区：{ws}")

    try:
        changed = _changed_paths(ws)
    except (subprocess.CalledProcessError, RuntimeError) as exc:
        return IntegrationResult(False, f"读取工作区状态失败：{exc}")

    if not changed:
        # ★ 不是失败：评审类 packet 本来就可能一个文件都不改。
        return IntegrationResult(True, f"{packet_id} 没有代码改动，无需合入")

    outside = _outside_owned(changed, owns_paths)
    if outside:
        return IntegrationResult(
            False,
            f"拒绝合入：{packet_id} 改了自己不拥有的路径（I1 单写者）：\n"
            + "\n".join(f"  · {p}" for p in sorted(outside)[:10])
            + f"\n它声明的 ownsPaths 是 {list(owns_paths)}。",
        )

    # ★ 项目工作区必须干净 —— 那是使用者的工作目录。
    try:
        dirty = _changed_paths(repo)
    except (subprocess.CalledProcessError, RuntimeError) as exc:
        return IntegrationResult(False, f"读取项目状态失败：{exc}")
    if dirty:
        return IntegrationResult(
            False,
            "拒绝合入：项目工作区有未提交的改动，自动合并可能冲掉它们：\n"
            + "\n".join(f"  · {p}" for p in sorted(dirty)[:10])
            + "\n请先提交或暂存这些改动。",
        )

    try:
        _git(ws, "add", "-A")
        _git(
            ws, "-c", "user.name=Codentum", "-c", "user.email=codentum@localhost",
            "commit", "-m", f"feat({packet_id}): worker 产出",
        )
        sha = _git(ws, "rev-parse", "HEAD").stdout.strip()
    except subprocess.CalledProcessError as exc:
        return IntegrationResult(False, f"在工作区提交失败：{(exc.stderr or exc.stdout).strip()}")

    merged = _git(
        repo, "merge", "--no-ff", sha, "-m", f"merge({packet_id}): 合入 worker 产出",
        check=False,
    )
    if merged.returncode != 0:
        # ★ 中止并还原 —— 留一个冲突中的仓库给使用者，比不合入糟得多。
        _git(repo, "merge", "--abort", check=False)
        return IntegrationResult(
            False,
            f"合并冲突，已中止：{(merged.stderr or merged.stdout).strip()[:400]}\n"
            "★ I1（路径独占）成立时不该出现冲突 —— 出现了说明前面某条不变量已经破了。",
        )

    return IntegrationResult(
        True, f"{packet_id} 的 {len(changed)} 处改动已合入项目", commit=sha
    )
