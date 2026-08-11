"""盯住「测试目录漏登记」这一类静默缺陷。

════════════════════════════════════════════════════════════════
 这条测试是怎么来的
════════════════════════════════════════════════════════════════

2026-08-10 接引擎入口时顺手发现：`pyproject.toml` 的 `testpaths` 漏了
`packages/delivery/tests`，于是 **C 的 15 条测试从来没有进过 `make test`**。

讽刺的地方在于，Makefile 里早就写着正确的判断：

    ★ 不写死 tests/contract。写死的话，新增的测试目录会静默地不被
      verify-offline 覆盖 —— 而「没跑过的测试」和「跑过且通过的测试」
      在终端上长得一模一样。收集范围交给 pyproject 的 testpaths，
      保持单一来源。

判断是对的，做法只做了一半：**单一来源本身仍然是手维护的。**
新增一个包的时候没人记得回来改它，而漏掉的后果**不会有任何症状** ——
终端照样打印「全部通过」，只是少数了十几条。

这正是本项目反复记的那条：**零输入的绿灯最危险。**
一个从未被收集的测试文件，和一个通过的测试文件，在报告里无法区分。

★ 所以判据不能是「我记得登记了」，得是「漏登记会有东西变红」。
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _configured_testpaths() -> set[str]:
    raw = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    paths = raw["tool"]["pytest"]["ini_options"]["testpaths"]
    return {p.replace("\\", "/").rstrip("/") for p in paths}


def _existing_package_test_dirs() -> set[str]:
    return {
        d.relative_to(ROOT).as_posix()
        for d in (ROOT / "packages").glob("*/tests")
        if d.is_dir() and any(d.glob("test_*.py"))
    }


def test_every_package_test_directory_is_collected() -> None:
    """★ 新增 packages/<名>/tests 而忘了登记 testpaths → 这条变红。

    不写成「检查某几个已知目录」，是因为那样只能防住已经出过问题的那几个，
    防不住下一个新包 —— 而下一个新包正是本次出问题的方式。
    """

    configured = _configured_testpaths()
    existing = _existing_package_test_dirs()
    missing = sorted(existing - configured)
    assert not missing, (
        f"这些测试目录存在但没有登记进 pyproject 的 testpaths，"
        f"`make test` 不会收集它们（而终端上看不出区别）：{missing}"
    )


def test_configured_testpaths_all_exist() -> None:
    """★ 反向：登记了一个不存在的目录。

    pytest 对不存在的 testpath 只是忽略，不报错 —— 于是一次目录改名
    可以让整块测试静默消失，而配置文件看起来仍然是完整的。
    """

    stale = sorted(p for p in _configured_testpaths() if not (ROOT / p).is_dir())
    assert not stale, f"testpaths 里这些目录不存在（pytest 会静默忽略）：{stale}"


def test_makefile_does_not_hardcode_test_paths() -> None:
    """★ 对照：Makefile 一旦把路径写回去，单一来源就又碎了。

    这条守的是 Makefile 里那句注释所表达的意图本身。
    """

    makefile = (ROOT / "Makefile").read_text("utf-8")
    test_target = re.search(r"^test:\n((?:\t.*\n)+)", makefile, re.MULTILINE)
    assert test_target, "Makefile 里找不到 test 目标"
    body = test_target.group(1)
    assert "packages/" not in body and "tests/contract" not in body, (
        f"Makefile 的 test 目标把测试路径写死了，testpaths 就不再是单一来源：\n{body}"
    )
