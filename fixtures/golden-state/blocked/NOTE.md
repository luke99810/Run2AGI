# 快照：blocked

**代表的情况**：三种"不顺利"同时存在——卡住待升级、待人工审批、预算超支告警。
异常态的渲染用例。

**产生方式**：手工构造。

**场景设定**（承接 mid-flight 的同一个记账应用，往后走了一段）：

```
wp-a1c001  contract  accepted   architect   契约冻结（同 mid-flight）
wp-b2d002  impl      blocked    coder       ★ 试了 3 次没过，已升到 L1 Helper
wp-c3e003  review    running    reviewer    评审中 ★ 用的模型 ≠ coder 的
wp-d4f004  impl      running    coder       正常跑
wp-f6h006  fix       pending    coder       ★ 待人工审批（改了契约相关的东西）
```

**关键字段**：
- `budget.json:spentUsd` = `18.40` / `limitUsd` = `20` → **剩 1.60**
  `alerts` 里有一条 `warn`。★ **故意接近上限但没超**——
  想测"超支"的话请另开快照，不要把这个改成负数
- `wp-b2d002:attempts` = `3`，`state` = `blocked`
  → 分级求助已升到 L1，`decisions.jsonl` 里有完整的升级轨迹
- `wp-c3e003` 的 reviewer 用 `claude-sonnet-5`，而它评审的 coder 用 `claude-opus-5`
  → ★ **模型隔离的正例**。前端的证据/审计视图应该能显示这个区分
- `ownership.locks` 只有 **2 条** —— blocked 的 packet **已释放锁**
  ★ 这是关键：卡住不等于占着锁不放，否则整条流水线会被一个卡住的任务锁死
- `wp-f6h006` 的 `acceptance.kind` = `manual` → 待人工审批队列里应出现它

**这个快照存在的理由**：

**Demo 现场最怕的不是失败，是失败之后界面上什么都看不出来。**

评委真正想看的是"出问题时系统怎么表现"——升级到第几级、谁在等审批、
钱还剩多少。这个快照就是那一屏。

**已知不完整之处**：
- 没有构造"预算已超支"的情况（`spentUsd > limitUsd`），需要时另开 `over-budget/`
- `knowledge/` 仍为空
