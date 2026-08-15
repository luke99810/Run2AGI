"""Git worktree isolation for local workers."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from shutil import which

__all__ = [
    "GitWorktreeManager",
    "ProjectInit",
    "WorktreeIsolationError",
    "ensure_project_initialized",
]


class WorktreeIsolationError(RuntimeError):
    """A worker workspace cannot be prepared safely."""


class GitWorktreeManager:
    """Create one isolated Git worktree per worker attempt."""

    def __init__(self, repo_root: Path | str) -> None:
        self.repo_root = self._resolve_git_root(Path(repo_root))

    def create(self, workspace: Path | str, *, ref: str = "HEAD") -> Path:
        """Create an isolated worktree at `workspace`.

        The workspace must live outside the source repository. If it were nested
        under the repo, worker writes could leak into the controller checkout and
        break the physical isolation this layer is supposed to provide.
        """
        path = Path(workspace).expanduser()
        if not path.is_absolute():
            path = self.repo_root / path
        path = path.resolve()
        self._reject_inside_repo(path)
        if path.exists() and any(path.iterdir()):
            raise WorktreeIsolationError(f"worker workspace is not empty: {path}")

        if ref == "HEAD" and not self._has_commits():
            # ★ git 原文是 `fatal: invalid reference: HEAD` —— 那对写代码的人
            #   都不够直白，对「打开一个空文件夹提需求」的使用者更是无从下手。
            #   而这条路径**是新项目的正常起点**，不是异常输入。
            raise WorktreeIsolationError(
                f"项目仓库 {self.repo_root} 没有任何提交，无法建立隔离工作区。\n"
                "★ worktree 需要一个可检出的提交作为起点；刚 git init 的仓库还没有。\n"
                "→ 调用 ensure_project_initialized(项目根目录) 建立首个提交后再试"
                "（引擎启动时会自动做这件事）。"
            )

        path.parent.mkdir(parents=True, exist_ok=True)
        self._git(["worktree", "add", "--detach", str(path), ref])
        return Path(str(path).replace("\\", "/"))

    def _has_commits(self) -> bool:
        try:
            self._git(["rev-parse", "--verify", "HEAD"])
        except WorktreeIsolationError:
            return False
        return True

    def remove(self, workspace: Path | str) -> None:
        """Remove a worker worktree."""
        path = Path(workspace).expanduser().resolve()
        self._git(["worktree", "remove", "--force", str(path)])

    def _reject_inside_repo(self, workspace: Path) -> None:
        if workspace == self.repo_root or self.repo_root in workspace.parents:
            raise WorktreeIsolationError(
                f"worker workspace must be outside repo root {self.repo_root}: {workspace}"
            )

    def _git(self, args: list[str]) -> str:
        try:
            return subprocess.run(  # noqa: S603 - fixed executable and argument list, no shell.
                [_git(), "-C", str(self.repo_root), *args],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            ).stdout
        except FileNotFoundError as exc:
            raise WorktreeIsolationError("git executable not found") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout).strip()
            raise WorktreeIsolationError(detail or f"git command failed: {' '.join(args)}") from exc

    @classmethod
    def _resolve_git_root(cls, path: Path) -> Path:
        if not path.exists():
            raise WorktreeIsolationError(f"not a git repository: {path}")

        try:
            out = subprocess.run(  # noqa: S603 - fixed executable and argument list, no shell.
                [_git(), "-C", str(path), "rev-parse", "--show-toplevel"],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            ).stdout
        except FileNotFoundError as exc:
            raise WorktreeIsolationError("git executable not found") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout).strip()
            raise WorktreeIsolationError(detail or f"not a git repository: {path}") from exc
        return Path(out.strip()).resolve()


def _git() -> str:
    exe = which("git")
    if exe is None:
        raise WorktreeIsolationError("git executable not found")
    return exe


@dataclass(frozen=True, slots=True)
class ProjectInit:
    """一次项目初始化的结果。

    ★ `changed` 与 `detail` 都要返回，而不是只返回布尔：
      调用方需要把「到底做了什么」如实说出去。
      一个悄悄改了用户仓库的初始化，比一个失败的初始化更难查。
    """

    changed: bool
    detail: str


def ensure_project_initialized(root: Path | str) -> ProjectInit:
    """确保项目可被 worktree 隔离使用 —— 必要时建立首个提交。

    ════════════════════════════════════════════════════════════
     ★ 这条路径是新项目的**正常起点**，不是异常输入
    ════════════════════════════════════════════════════════════

    桌面端最常见的用法就是「打开一个新文件夹 → 提一个需求」。
    而 `git worktree add --detach <path> HEAD` 在**一个提交都没有**的仓库上
    必然失败（`fatal: invalid reference: HEAD`）。

    后果不是崩溃，是 packet 永远停在 ready —— 调和循环看不到任何转换，
    就认为系统已经稳定并正常退出。**使用者只看到「什么都没发生」。**

    ════════════════════════════════════════════════════════════
     ★ 只在两种情况下动手，第三种一律不碰
    ════════════════════════════════════════════════════════════

    | 现状 | 行为 |
    |---|---|
    | 不是 git 仓库 | `git init` + 首个提交 |
    | 是 git 仓库，但**一个提交都没有** | 首个提交 |
    | 是 git 仓库，**已有提交** | **什么都不做** |

    第三条是**安全判据**，不是优化：在使用者已有的仓库历史上加一个提交，
    是不可接受的副作用。有人把 Codentum 指向一个真实项目时，
    它绝不能往那段历史里写东西。

    ★ 首个提交用 `git add -A` 把现有内容纳进来，而不是建一个空提交：
      worktree 是从这个提交检出的 —— 空提交会让 worker 看到一个**空工作区**，
      而使用者明明把文件放在那里了。
      配合 `--allow-empty`，空目录与有内容的目录都能覆盖。
    """

    path = Path(root).expanduser().resolve()
    if not path.is_dir():
        raise WorktreeIsolationError(f"项目根目录不存在：{path}")

    def run(*args: str, allow_fail: bool = False) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - 固定可执行文件与参数表，无 shell
            [_git(), "-C", str(path), *args],
            check=not allow_fail,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    inside = run("rev-parse", "--is-inside-work-tree", allow_fail=True)
    is_repo = inside.returncode == 0 and inside.stdout.strip() == "true"

    if is_repo and run("rev-parse", "--verify", "HEAD", allow_fail=True).returncode == 0:
        return ProjectInit(changed=False, detail=f"已是含提交的 git 仓库，未做任何改动：{path}")

    actions: list[str] = []
    if not is_repo:
        run("init")
        actions.append("git init")

    # ★ 身份缺失时用一次性的 `-c` 覆盖，**不写进使用者的 git config**。
    #   全新环境常常没配 user.email，而那会让 commit 直接失败 ——
    #   一个「初始化」步骤因为环境没配好而失败，等于没有这个步骤。
    identity: list[str] = []
    if not run("config", "user.email", allow_fail=True).stdout.strip():
        identity = ["-c", "user.name=Codentum", "-c", "user.email=codentum@localhost"]

    run("add", "-A")
    subprocess.run(  # noqa: S603
        [_git(), "-C", str(path), *identity, "commit", "--allow-empty", "-m",
         "chore: Codentum 初始化项目（建立 worktree 隔离所需的首个提交）"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    actions.append("首个提交")
    return ProjectInit(changed=True, detail=f"{' + '.join(actions)}：{path}")
