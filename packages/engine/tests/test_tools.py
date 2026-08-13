"""工具执行器的判据。

★ 这些工具是**唯一能让模型真正改动磁盘**的东西，所以边界必须有测试守着：
  越界写一次就可能污染真实仓库，而 worker 的隔离承诺就此作废。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from codentum_engine.tools import MAX_WRITE_BYTES, ToolExecutor, tool_schemas_for


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


# ══════════════════════════════════════════════════════════════
#  安全边界
# ══════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "escape",
    [
        "../outside.py",
        "../../outside.py",
        "workspace/../../outside.py",
        "./../outside.py",
    ],
)
def test_write_cannot_escape_the_workspace(workspace: Path, escape: str) -> None:
    """★ 路径穿越必须被拒，而不是「警告后放行」。

    worker 拿到的是隔离工作区；越界写会污染真实仓库，
    而那正是 I1 单写者想防的事 —— 一旦发生，锁机制就是摆设。
    """

    executor = ToolExecutor(workspace)
    result = executor.execute("write_file", {"path": escape, "content": "x"})

    assert result.ok is False, f"{escape} 被放行了"
    assert "越出工作区" in result.content
    assert executor.written_paths == []
    assert not (workspace.parent / "outside.py").exists()


def test_absolute_path_is_rejected(workspace: Path) -> None:
    """★ 绝对路径同样要挡。"""

    target = workspace.parent / "absolute.py"
    executor = ToolExecutor(workspace)
    result = executor.execute("write_file", {"path": str(target), "content": "x"})

    assert result.ok is False
    assert not target.exists()


def test_oversized_write_is_refused_not_truncated(workspace: Path) -> None:
    """★ 超限要**拒绝**，不要截断后报成功。

    截断后报成功，模型会以为整份文件写进去了，接着基于一个不存在的
    完整文件继续往下做 —— 那比直接失败难查得多。
    """

    executor = ToolExecutor(workspace)
    result = executor.execute("write_file", {"path": "big.py", "content": "x" * (MAX_WRITE_BYTES + 1)})

    assert result.ok is False
    assert "超过上限" in result.content
    assert not (workspace / "big.py").exists()


# ══════════════════════════════════════════════════════════════
#  正常路径
# ══════════════════════════════════════════════════════════════


def test_write_then_read_round_trips(workspace: Path) -> None:
    executor = ToolExecutor(workspace)
    code = "def monthly_total(subs):\n    return sum(s.monthly_cny for s in subs)\n"

    written = executor.execute("write_file", {"path": "workspace/app.py", "content": code})
    assert written.ok is True
    assert (workspace / "workspace" / "app.py").read_text(encoding="utf-8") == code
    assert executor.written_paths == ["workspace/app.py"]

    read = executor.execute("read_file", {"path": "workspace/app.py"})
    assert read.ok is True
    assert read.content == code


def test_written_paths_is_the_evidence_of_having_worked(workspace: Path) -> None:
    """★ `written_paths` 是「干没干活」的第一手证据 —— 控制平面的
    `touched_paths` 判据最终看的就是这类信号。重复写同一文件不重复计数。"""

    executor = ToolExecutor(workspace)
    executor.execute("write_file", {"path": "a.py", "content": "1"})
    executor.execute("write_file", {"path": "a.py", "content": "2"})
    executor.execute("write_file", {"path": "b.py", "content": "3"})

    assert executor.written_paths == ["a.py", "b.py"]


def test_read_missing_file_says_so_instead_of_returning_empty(workspace: Path) -> None:
    """★ 文件不存在必须**明说**。返回空字符串会让模型以为文件是空的。"""

    result = ToolExecutor(workspace).execute("read_file", {"path": "nope.py"})
    assert result.ok is False
    assert "不存在" in result.content


def test_run_tests_reports_exit_code(workspace: Path) -> None:
    executor = ToolExecutor(workspace)
    ok = executor.execute("run_tests", {"command": [sys.executable, "-c", "print('hi')"]})
    assert ok.ok is True
    assert "退出码 0" in ok.content

    bad = executor.execute("run_tests", {"command": [sys.executable, "-c", "raise SystemExit(3)"]})
    assert bad.ok is False
    assert "failure_type=test" in bad.content
    assert "退出码 3" in bad.content


def test_run_build_has_separate_failure_type_and_dependency_context(workspace: Path) -> None:
    (workspace / "pyproject.toml").write_text("[project]\nname = \"demo\"\n", encoding="utf-8")
    executor = ToolExecutor(workspace)

    ok = executor.execute("run_build", {"command": [sys.executable, "-c", "print('built')"]})
    assert ok.ok is True
    assert "构建成功" in ok.content
    assert "built" in ok.content

    bad = executor.execute("run_build", {"command": [sys.executable, "-c", "raise SystemExit(7)"]})
    assert bad.ok is False
    assert "failure_type=build" in bad.content
    assert "退出码 7" in bad.content
    assert "pyproject.toml" in bad.content
    assert "failure_type=test" not in bad.content


@pytest.mark.parametrize(
    "command",
    [
        [sys.executable, "-m", "pip", "install", "requests"],
        ["pip", "install", "requests"],
        ["npm", "install"],
        ["pnpm", "add", "lodash"],
        ["uv", "pip", "install", "requests"],
        ["poetry", "add", "requests"],
    ],
)
def test_dependency_install_commands_are_blocked(workspace: Path, command: list[str]) -> None:
    executor = ToolExecutor(workspace)

    test_result = executor.execute("run_tests", {"command": command})
    build_result = executor.execute("run_build", {"command": command})

    assert test_result.ok is False
    assert build_result.ok is False
    assert "dependency_install_boundary" in test_result.content
    assert "dependency_install_boundary" in build_result.content
    assert "依赖清单" in build_result.content


def test_run_tests_does_not_inherit_stdin(workspace: Path) -> None:
    """★ 子进程若读 stdin 会挂住整个 worker。

    这个坑 2026-08-11 已经在引擎入口踩过一次（git 继承协议管道，卡死 240 秒）。
    """

    result = ToolExecutor(workspace).execute(
        "run_tests", {"command": [sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"]}
    )
    assert result.ok is True, result.content
    assert "''" in result.content  # 读到 EOF，不是挂住


def test_unknown_tool_fails_without_raising(workspace: Path) -> None:
    """★ 未知工具返回失败而不是抛异常 —— 抛异常会中断整个工具循环。"""

    result = ToolExecutor(workspace).execute("rm_rf", {"path": "/"})
    assert result.ok is False
    assert "未知工具" in result.content


# ══════════════════════════════════════════════════════════════
#  工具面
# ══════════════════════════════════════════════════════════════


def test_only_whitelisted_tools_are_exposed() -> None:
    """★ 与 `derive_tool_surface` 同一条规矩：未授权的工具**不出现在清单里**，
    而不是调用时才拒绝 —— 「无权限」先表现为「看不见」。"""

    schemas = tool_schemas_for(("read_file", "list_files"))
    assert {s.name for s in schemas} == {"read_file", "list_files"}


def test_declared_but_unimplemented_tools_are_skipped_silently() -> None:
    """★ RoleSpec 里声明了、但还没实现的工具要跳过，不能伪造一个空壳。

    让模型看见一个点了没反应的工具，比让它看不见更糟 ——
    这与桌面端「未接入的不得显示为可用」是同一条规矩。
    """

    schemas = tool_schemas_for(("write_file", "create_diff", "尚未实现的工具"))
    assert {s.name for s in schemas} == {"write_file"}


def test_every_schema_declares_its_required_inputs() -> None:
    """★ 少了 required，模型可以合法地不传参数，然后工具在运行时才炸。"""

    for schema in tool_schemas_for(("write_file", "read_file", "run_tests", "run_build", "request_help")):
        assert schema.description.strip(), f"{schema.name} 没有描述，模型不知道何时该用它"
        assert schema.input_schema.get("required"), f"{schema.name} 没声明必填参数"
