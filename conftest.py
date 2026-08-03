"""pytest 全局导入路径。

════════════════════════════════════════════════════════════════
 为什么需要它
════════════════════════════════════════════════════════════════

各包按 `packages/<名>/<import 根>/` 布局（契约层还多一层 `python/`，因为
同一份 schema 要往 Python 和 TypeScript 两侧发类型）。这个布局对跨语言
是对的，但它不是 Python 的默认可导入形态。

在没有 `pip install -e .` 的机器上 —— 而第 0 周的承诺恰恰是
「verify-offline 零第三方依赖，pip install 之前就能跑」—— 必须有人把这
几个根挂到 sys.path 上。

生成的契约测试目前是每个文件自己 insert 一遍。那是生成器的产物，改它要
改生成器；手写测试没有理由跟着重复这段样板，所以集中在这里。

★ 只加路径，不做任何别的事。conftest 里放副作用会让测试的可复现性依赖
  于 pytest 的收集顺序，而那是最难查的一类不稳定。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# 顺序无关紧要，各自的顶层包名不重叠
_IMPORT_ROOTS = (
    ROOT / "packages" / "contracts" / "python",
    ROOT / "packages" / "control-plane",
    ROOT / "packages" / "harness",
    ROOT / "packages" / "roles",
    ROOT / "packages" / "delivery",
    ROOT / "scripts",
)

for _root in _IMPORT_ROOTS:
    if _root.is_dir():
        path = str(_root)
        if path not in sys.path:
            sys.path.insert(0, path)
