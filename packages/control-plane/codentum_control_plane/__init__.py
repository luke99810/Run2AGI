"""codentum_control_plane —— 控制平面

决定「下一步做什么、谁能做、能不能过」。

★★ 这个包里不允许出现任何模型调用。一行都不行。

  风险分配原则：不可恢复的错误交给确定性代码，可恢复的错误才交给模型。
  锁判错就是数据损坏，没有重试能救回来；代码写错了，测试会打回来重写。

★ 不 import codentum_harness。
  控制平面必须能在没有执行体的情况下推进状态、跑完整测试。
  而且执行体一旦知道调度逻辑，就会开始【迎合】它。

★ 不用 LangGraph（见 ADR-0004）。
  调和循环对标 K8s controller manager，是确定性代码；
  LangGraph 的 checkpointer 想要自己的持久化，会和"Git 是唯一状态源"打架。

owner: A ｜ 评审: B ｜ 详见 ../README.md

── 待实现 ──────────────────────────────────────────────
  reconcile/       调和循环 —— ★ 必须幂等，否则崩溃恢复无从谈起
  state_machine/   状态转换 —— 转换表从 RoleSpec 派生，不手写
  locks/           路径锁 —— ★ I1 的实现，判错即数据损坏
  admission/       准入校验 —— 拒绝在前，执行在后
  gates/           门禁 —— 判定必须可复算
  budget/          预算记账 —— ★ 货币计，不用 token
  guardian/        确定性拦截 —— ★ 无 LLM，拦截率要回流进化层
"""
