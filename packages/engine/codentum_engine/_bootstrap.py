"""让引擎不依赖 `PYTHONPATH` 就能被拉起来。

════════════════════════════════════════════════════════════════
 ★ 为什么需要它
════════════════════════════════════════════════════════════════

引擎是被 sidecar 用 `CODENTUM_ENGINE_COMMAND_JSON` 里的 argv 拉起来的，
而那条 argv 长这样：`["python", "-m", "codentum_engine", ...]`。
`-m` 要求 `codentum_engine` 已经在 `sys.path` 上 —— 也就是要求**启动
Electron 的那个终端事先 export 过 PYTHONPATH**。

这个前提在开发机上很容易不成立：换个终端、双击启动、从 IDE 里跑、
或者别人 clone 下来照着 README 跑，都不会有 PYTHONPATH。
而它不成立时的现象是**引擎进程连启动都没启动**，于是：

  - 引擎自己的日志一个字都没有（进程根本没跑到写日志那一步）
  - `JsonlEngineProxy._drain_stderr` 有意丢弃 stderr 文本
  - 桌面端最终只显示一句 "A/B engine handshake failed"

**三层之中没有任何一层知道真因。** 2026-08-11 就在这上面耗了一整轮排查。

★ 解决方向不是「把环境配对」，是**让它不需要被配对**：
  引擎从自己的 `__file__` 推出仓库根，把五个包根挂上 `sys.path`。
  此后 `python <绝对路径>/codentum_engine/__main__.py` 就能跑，
  不需要 PYTHONPATH、不需要特定 cwd、不需要 `-m`。

★ 只加路径，不做任何别的事 —— 与根目录 `conftest.py` 同一条规矩：
  引导逻辑里放副作用，会让「能不能启动」依赖于导入顺序，
  而那是最难查的一类不稳定。
"""

from __future__ import annotations

import sys
from pathlib import Path

__all__ = ["ensure_packages_importable"]

# packages/engine/codentum_engine/_bootstrap.py → 仓库根要上跳四层
_REPO_ROOT = Path(__file__).resolve().parents[3]

_PACKAGE_ROOTS = (
    _REPO_ROOT / "packages" / "contracts" / "python",
    _REPO_ROOT / "packages" / "control-plane",
    _REPO_ROOT / "packages" / "harness",
    _REPO_ROOT / "packages" / "roles",
    _REPO_ROOT / "packages" / "delivery",
    _REPO_ROOT / "packages" / "engine",
)


def ensure_packages_importable() -> None:
    """把仓库内的包根补进 `sys.path`（已在的不重复加）。

    ★ 追加到末尾而不是插到开头：如果调用方已经用 PYTHONPATH 或
      `pip install -e` 指定了某个版本，那是更明确的意图，不该被这里覆盖。
      这个函数只负责「没人配的时候也能跑」。
    """

    for root in _PACKAGE_ROOTS:
        if not root.is_dir():
            # 打包成单文件后目录结构会变，那时依赖打包器自己的导入机制。
            continue
        text = str(root)
        if text not in sys.path:
            sys.path.append(text)
