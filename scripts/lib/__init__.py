"""scripts 的共用小工具。**零第三方依赖**，`pip install` 之前就能跑。

════════════════════════════════════════════════════════════════
 ★ 这个文件为什么存在（它看起来是空的）
════════════════════════════════════════════════════════════════

没有它的时候，`make typecheck`（`mypy packages scripts`）**整个跑不起来**：

    scripts\\lib\\console.py: error: Source file found twice under different
    module names: "console" and "lib.console"
    Found 1 error in 1 file (errors prevented further checking)

注意最后半句 —— **errors prevented further checking**。
不是「检查完发现 1 个问题」，是**一个文件都没检查**就退出了。

于是 `make typecheck` 从很早以前就一直是这个状态，没人发现。原因是它
**报错**而不是**变红**：终端上滚过去一行红字，退出码非 0，
但没有任何测试会因此失败，也没有人把它当成「门禁挂了」。

★ 这是本项目反复记的那件事的又一个变体：
  §十九 是「零输入的绿灯」，这里是「零输入的红灯」——
  一个从未真正执行过的检查，和一个执行了并通过的检查，
  在 CI 日志里的区别只有一行字。

运行时行为不受影响：`scripts/` 本来就在 `sys.path` 上
（`conftest.py` 加的），`from lib.console import ...` 加不加这个文件都能 import。
它只是把 `lib` 从「隐式命名空间包」变成「常规包」，
让 mypy 不再对同一个文件产生两种模块名。
"""
