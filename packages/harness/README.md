# harness —— 执行外壳

单次执行的受控外壳。**一次执行的可靠性**由这里负责；多次执行的收敛由控制平面负责。

---

## 职责

**做什么**：把一个 WorkPacket 变成一次受控的模型执行——装上下文、给工具、隔离工作区、记检查点、收结果、留证据。

**明确不做什么**：
- ✗ 不决定下一步做什么（那是 `control-plane/reconcile`）
- ✗ 不判定验收通过与否（那是 `control-plane/gates`）
- ✗ 不管重试几次、要不要升级（同上）

**为什么这条界线要守死**：执行体一旦知道调度逻辑，就会开始**迎合**它——比如察觉"再失败一次就要升级了"而修改行为。隔离不是洁癖，是防止 Agent 优化错误的目标。

---

## 归属

| | |
|---|---|
| owner | **B** |
| 评审 | A（不变量守护者） |
| 依赖 | `contracts` + `roles`（加载 RoleSpec）；**不依赖 control-plane** |

---

## 目录

| 目录 | 职责 |
|---|---|
| `context-broker/` | 可见性矩阵 + 配方 + 预算降级链 + 检索确定性梯度 |
| `tool-surface/` | 从 RoleSpec 派生工具面。**角色看不见的工具不出现在列表里** |
| `worker/` | 执行体封装：Solo 的 Git worktree 隔离、Team 的 AgentTeams Worker 资源适配、生命周期 |
| `runner/` | Worker 的真实执行适配器；P0 先提供本地命令 Runner 与 ModelGatewayRunner，后续接百炼 / Hermes / Claude Code |
| `prompt_bundle/` | 把已强制过的 RoleSpec / SpawnRequest / ContextBundle 稳定渲染成模型输入包 |
| `checkpoint/` | 执行中断点与恢复 |
| `replay/` | 回放：同样的输入能重建同样的执行上下文 |

---

## 组装入口

`codentum_harness.runtime` 是产品侧 / 演示脚本的 composition root：
`LocalWorkerRuntimeConfig` + `RunnerConfig` → `build_local_worker_runtime()`；
`TeamWorkerRuntimeConfig` → `build_team_worker_runtime()`。
控制平面仍然只拿到冻结的 `WorkerRuntime`，不需要知道执行体是 Solo 的本地 worktree，还是 Team 的 AgentTeams Worker。

本地命令 Runner 支持 `{prompt_dir}`、`{system_prompt}`、`{user_prompt}`、`{prompt_manifest}`
等占位符，外部编码 Agent 命令可直接读取已落盘的 Prompt Bundle。

`ModelGatewayRunner` 则读取同一份 Prompt Bundle，经冻结的 `ModelGateway` 发起一次模型调用，
并把模型响应、usage、tool_calls 与 prompt digest 写入 `model/` 证据目录。

`TeamWorkerRuntime` 目前是 AgentTeams 适配的最小壳：从同一个 `SpawnRequest` 写 manifest /
checkpoint-0 / Prompt Bundle，再通过官方 `agt` CLI 创建并检查 AgentTeams Worker 资源。
在任务派发和结果回收接上之前，`settle()` 会返回 `WorkerFailed(runtime_error)`，
并附上 `file:agentteams/status.json` 或 `file:agentteams/error.json` 证据，避免把“Worker 资源已 Running”
误报成“Codentum 任务已完成”。

---

## 核心结构：三段式，只有中段有模型

```
  准备阶段            →     模型阶段      →      收敛阶段
  ─────────                 ────────             ────────
  【无模型】                 【有模型】            【无模型】
  装配上下文                  Agent 干活          解析产出
  派生工具面                                      写证据
  挂载卷（只读/可写）                              判定形式合规
  申请路径锁
```

**进模型前，约束已经物理生效；出模型后，判定由代码做出。**

这是全案最重要的实现原则之一，对应约束优先级：

```
不可见  >  无权限  >  被拦截  >  提示词劝阻
   ↑                              ↑
最可靠                          最不可靠
```

提示词里写"请不要修改测试文件"是最弱的一档。正确做法是**把测试目录以只读方式挂载**——它在物理上就改不了，不需要 Agent 配合。

---

## 完成定义

```
□ 同一 WorkPacket 执行两次，装配出的上下文逐字节相同（可复现）
□ ★ 角色无权限的工具不出现在工具列表里（不是拒绝调用，是根本看不见）
□ ★ 只读挂载的路径写入必定失败，且失败被记录为证据
□ 执行中断后能从检查点恢复，不重头再来
□ replay 能用历史执行记录重建等价的上下文
□ 预算降级链：预算不足时按既定顺序降级，而不是随机截断
```

---

## 硬约束

1. **准备阶段与收敛阶段不含模型。** 违反了整个可靠性论证就塌了。
2. **不 import `control-plane`。** 见"职责"里的理由。
3. **工具面从 RoleSpec 派生，不手工维护。**
   RoleSpec 是 Single Source，要派生四处：工具面 / 卷挂载 / 状态转换 / 所有权注册。**任一处手工维护都会漂移**，而且漂移时通常不报错，只是权限悄悄变宽。
4. **上下文装配必须确定性可复现。** 不可复现 → replay 失效 → 出了问题查不了。
5. **⚠️ 同一次尝试内不要切换模型。** 提示词缓存是按模型分桶的，中途换模型 = 整个前缀冷重写。需要加强时**提高 effort，而不是换模型**——effort 变化不会让缓存失效。
6. **证据先写后返回。** 执行完成但证据没落盘，等于没做过。
