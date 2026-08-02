"""统一把 stdout/stderr 切成 UTF-8。

★ 为什么需要这个：Windows 控制台默认是 GBK，脚本里打一个 ✓ 或中文就会
  抛 UnicodeEncodeError —— 而且它抛在【打印结果的那一行】，也就是工作
  已经全部做完之后。于是看到的现象是"脚本崩了"，实际文件已经写好了。

  这种"崩在最后一步"的报错最容易误导人去查前面的逻辑。
  三个人都在 Windows 上开发，不堵一定会踩。

用法：每个脚本开头 `from lib.console import setup_console; setup_console()`
"""

from __future__ import annotations

import sys


def setup_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        enc = getattr(stream, "encoding", None)
        if enc and enc.lower().replace("-", "") != "utf8":
            try:
                stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
            except (AttributeError, OSError):
                pass  # 重定向到不支持 reconfigure 的流时，静默降级
