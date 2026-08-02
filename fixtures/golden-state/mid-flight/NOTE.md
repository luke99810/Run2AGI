# 快照：mid-flight

**代表的情况**：三个 Coder packet 并行执行中，一个已被接受，一个在排队等依赖。
主路径的典型状态——甘特图、依赖图、成本面板的主要渲染用例。

**产生方式**：手工构造（生成脚本落地后应改为从真实运行导出）。

**场景设定**（一个带认证的记账应用，切成四块）：

```
wp-a1c001  contract  accepted   architect   契约已冻结 ★ 后面三个都依赖它
wp-b2d002  impl      running    coder       认证模块      持锁 src/auth/
wp-c3e003  impl      running    coder       账目 API      持锁 src/api/
wp-d4f004  impl      running    coder       前端          持锁 src/web/
wp-e5g005  integrate pending    integrator  集成          等前面三个
```

**关键字段**：
- `ownership.locks` 有 **3 条**，`pathPrefix` **两两不相交** —— ★ 这是 I1 单写者的正例
- `ownership.version` = `7` —— 非零，验证前端不会把 version 当布尔用
- `dependency.edges` 构成 DAG：a1c001 → 三个 impl → e5g005
- `budget.json:spentUsd` = `4.82`，`byRole` / `byModel` 都非空
- ★ `byModel` 里 **coder 与 reviewer 用的是不同模型** —— 模型隔离的正例，
  前端的成本面板应能呈现这个区分

**两处刻意埋的边界，前端不要"顺手修掉"**：

1. **`wp-e5g005` 的 `ownsPaths` 是 `src/`，是那三把锁的前缀。**
   看起来像冲突，其实不是 —— 它 `state` 是 `pending`，**锁只对 running 的 packet 生效**。
   这正是集成 packet 的正常形态：它要等三个 impl 都释放锁才能进入 running。
   ★ 如果前端把它标红报"锁冲突"，就是把待调度状态误判成错误状态。

2. **`decisions.jsonl` 里有一条 `guardian / tool_blocked`** ——
   Coder 试图写只读挂载的 `tests/`，被确定性拦截。
   ★ 这不是错误，是**防作弊机制正常工作的证据**。审计视图应该把它显示为
   "已拦截"而不是"失败"。这条记录还要回流给进化层做拦截率统计。

**这个快照存在的理由**：

它是**并行安全的可视化证据**。三把锁互不相交、三个 packet 同时 running ——
Demo 现场评委看到的就是这一屏。前端如果连这个都渲染不对，
全案最核心的论证就没法被看见。

**已知不完整之处**：
- `knowledge/` 为空 —— 知识图的渲染另开快照，不混在主路径里
- 证据只放了 1 条（已 accepted 的那个 packet 的），足够验证哈希链的链首形态
