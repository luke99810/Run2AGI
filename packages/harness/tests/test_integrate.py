"""产出合入的判据。

★ 这组守的是一句话：**「accepted」必须意味着东西真的进了项目。**
  缺了这一段，packet 显示验收通过，而使用者打开项目什么都没有 ——
  而那种失败没有任何东西会报错。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from codentum_harness.worker import GitWorktreeManager, integrate_worker_result
from codentum_harness.worker.worktree import ensure_project_initialized


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True, capture_output=True, text=True, encoding="utf-8",
    ).stdout


def _project(tmp_path: Path) -> Path:
    repo = tmp_path / "project"
    repo.mkdir()
    ensure_project_initialized(repo)
    return repo


def _worktree(repo: Path, tmp_path: Path, name: str = "wp-abc123") -> Path:
    return GitWorktreeManager(repo).create(tmp_path / "workers" / name)


# ══════════════════════════════════════════════════════════════
#  正向：东西真的进了项目
# ══════════════════════════════════════════════════════════════


def test_worker_changes_actually_land_in_the_project(tmp_path: Path) -> None:
    """★ 断言落在**项目目录里能读到那个文件**，而不是「函数返回了 True」。

    返回值是自述；文件在不在是事实。这一层要证明的正是「不是自述」。
    """

    repo = _project(tmp_path)
    ws = _worktree(repo, tmp_path)
    (ws / "workspace").mkdir(parents=True)
    (ws / "workspace" / "app.py").write_text("print('hi')\n", encoding="utf-8")

    result = integrate_worker_result(
        repo, ws, packet_id="wp-abc123", owns_paths=("workspace/",)
    )

    assert result.merged, result.detail
    assert (repo / "workspace" / "app.py").read_text(encoding="utf-8") == "print('hi')\n"


def test_no_changes_is_success_not_failure(tmp_path: Path) -> None:
    """★ 评审类 packet 本来就可能一个文件都不改。

    把「没有改动」和「合入失败」混成一个布尔，会让前者被当成故障 ——
    然后 packet 被转 blocked，而它其实干得挺好。
    """

    repo = _project(tmp_path)
    ws = _worktree(repo, tmp_path)

    result = integrate_worker_result(repo, ws, packet_id="wp-abc123", owns_paths=("workspace/",))
    assert result.merged is True
    assert result.commit is None
    assert "没有代码改动" in result.detail


def test_bookkeeping_is_not_treated_as_worker_output(tmp_path: Path) -> None:
    """★ `.codentum/` 是系统自己写进工作区的簿记，不是 worker 的产出。

    把它算成改动会让「一个文件都没写」的 packet 看起来干了活 ——
    这与 touched_paths 那个坑是同一个，只是换到了合入这一层。
    """

    repo = _project(tmp_path)
    ws = _worktree(repo, tmp_path)
    (ws / ".codentum" / "evidence").mkdir(parents=True)
    (ws / ".codentum" / "evidence" / "manifest.json").write_text("{}", encoding="utf-8")

    result = integrate_worker_result(repo, ws, packet_id="wp-abc123", owns_paths=("workspace/",))
    assert result.merged is True
    assert result.commit is None, "簿记被当成了 worker 的产出"


# ══════════════════════════════════════════════════════════════
#  三条拒绝，每一条都是安全判据
# ══════════════════════════════════════════════════════════════


def test_refuses_when_the_project_tree_is_dirty(tmp_path: Path) -> None:
    """★ 项目根目录是**使用者的工作目录**。

    在那里做 merge 可能冲掉他没提交的改动 —— 与
    `ensure_project_initialized` 拒绝碰有历史的仓库是同一条原则：
    **别人的东西，不经允许不动。**
    """

    repo = _project(tmp_path)
    ws = _worktree(repo, tmp_path)
    (ws / "workspace").mkdir(parents=True)
    (ws / "workspace" / "app.py").write_text("x\n", encoding="utf-8")

    # 使用者手上有没提交的改动
    (repo / "我的草稿.txt").write_text("别动我\n", encoding="utf-8")

    result = integrate_worker_result(repo, ws, packet_id="wp-abc123", owns_paths=("workspace/",))

    assert result.merged is False
    assert "未提交的改动" in result.detail
    assert "我的草稿.txt" in result.detail, "拒绝时必须说清是哪些文件挡住了"
    assert (repo / "我的草稿.txt").read_text(encoding="utf-8") == "别动我\n"


def test_refuses_changes_outside_owned_paths(tmp_path: Path) -> None:
    """★ I1（单写者）靠路径独占成立。

    worker 的工具边界只拦「越出工作区」，不拦「越出自己拥有的路径」——
    在 worktree 里那还只是本地文件，**一旦合进主项目就真的破坏了 I1**。

    ★ 拒绝而不是「悄悄只合法的那部分」：静默丢弃会让 worker 以为写成功了，
      而产出对不上，排查时完全看不出发生过什么。
    """

    repo = _project(tmp_path)
    ws = _worktree(repo, tmp_path)
    (ws / "workspace" / "mine").mkdir(parents=True)
    (ws / "workspace" / "mine" / "ok.py").write_text("ok\n", encoding="utf-8")
    (ws / "workspace" / "others").mkdir(parents=True)
    (ws / "workspace" / "others" / "stolen.py").write_text("bad\n", encoding="utf-8")

    result = integrate_worker_result(
        repo, ws, packet_id="wp-abc123", owns_paths=("workspace/mine/",)
    )

    assert result.merged is False
    assert "workspace/others/stolen.py" in result.detail
    assert not (repo / "workspace" / "mine" / "ok.py").exists(), (
        "越界时整批拒绝，不能只合法的那部分"
    )


def test_missing_workspace_is_reported_not_silently_succeeded(tmp_path: Path) -> None:
    """找不到工作区 ≠ 没有改动。前者是故障，后者是正常。"""

    repo = _project(tmp_path)
    result = integrate_worker_result(
        repo, tmp_path / "nowhere", packet_id="wp-abc123", owns_paths=("workspace/",)
    )
    assert result.merged is False
    assert "找不到" in result.detail


# ══════════════════════════════════════════════════════════════
#  并行：I1 成立时两个 packet 应当都能合入
# ══════════════════════════════════════════════════════════════


def test_two_packets_on_disjoint_paths_both_merge(tmp_path: Path) -> None:
    """★ 这是「多 Agent 并行开发」最后一段的可执行形态。

    I1 保证两个 running packet 的 ownsPaths 两两不相交 ——
    那么它们的产出就应当能**依次合入而不冲突**。
    合不进去说明前面某条不变量已经破了。
    """

    repo = _project(tmp_path)
    for name, module in (("wp-aaa111", "alpha"), ("wp-bbb222", "beta")):
        ws = _worktree(repo, tmp_path, name)
        (ws / "workspace" / module).mkdir(parents=True)
        (ws / "workspace" / module / "m.py").write_text(f"# {module}\n", encoding="utf-8")
        result = integrate_worker_result(
            repo, ws, packet_id=name, owns_paths=(f"workspace/{module}/",)
        )
        assert result.merged, f"{name} 没合进去：{result.detail}"

    assert (repo / "workspace" / "alpha" / "m.py").exists()
    assert (repo / "workspace" / "beta" / "m.py").exists()
