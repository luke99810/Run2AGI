# 快照：empty

**代表的情况**：刚 `codentum init`，还没有任何 packet。

**产生方式**：手工构造。

**关键字段**：
- `graph.json:dependency.nodes` = `[]` —— 空数组，不是 `null`
- `graph.json:ownership.locks` = `[]` —— 无任何锁
- `graph.json:ownership.version` = `0` —— 初始版本
- `budget.json:spentUsd` = `0`
- `packets/` `evidence/` 目录存在但为空
- `decisions.jsonl` 存在但零行

**这个快照存在的理由**：

★ **「没有数据」是所有渲染代码的第一个边界，也是最常被跳过的那个。**

甘特图拿到空数组、依赖图拿到零节点、成本面板拿到 0 —— 这三处最容易写出
`data[0].xxx` 然后运行时炸掉。`noUncheckedIndexedAccess` 能在编译期拦下一部分，
但拦不住 `.length - 1` 之类的算法错误。

**这个快照不是"最简单的用例"，它是最容易出 bug 的用例。**

**已知不完整之处**：无。
