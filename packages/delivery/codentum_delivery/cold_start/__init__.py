"""冷启动交付验证。

★ 这个子包里的 `verify.py` **只许 import 标准库** —— 它要在
  `python:3.11-slim` 里独立运行，那个镜像里没有 Codentum，也不该有：
  装了 Codentum 再去验交付包，验的就不是「从零」了。
  这条约束由 `test_cold_start_verify.py` 守着。

★ 所以这里也不 re-export `verify` 里的东西：一次 `from . import verify`
  会让「只依赖标准库」的检查更难写，而这个子包本来就只有一个入口。
"""

__all__: list[str] = []
