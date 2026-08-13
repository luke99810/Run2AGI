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
| `context_broker/` | 可见性矩阵 + 配方 + 预算降级链 |
| `memory_index/` | 冻结 `MemoryIndex` 的本地持久实现；消费被授权的知识资源并产出可复现 `indexVersion` |
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

Prompt Bundle 会从 `RoleSpec.promptRef` 读取角色提示词，并从 `RoleSpec.skills` 读取
state 为空或 `active` 的 Skill 正文。运行时优先使用项目共享空间
`.codentum/skills/shared/<id>/SKILL.md`；共享空间不存在时，才回退到内置
`packages/roles/skills/<id>/SKILL.md`。写出的 `prompt/manifest.json` 记录
`skill_refs` 与 `skill_source`，便于桌面端和评审只看证据就知道这次 Worker 实际带了哪些
Skill，以及这些 Skill 来自项目共享副本还是内置源。

`PersistentMemoryIndex` 是当前 B 侧的最小真实记忆索引：entry 以内容寻址
`mem:sha256:*` 存到 `.codentum/memory/index/entries/`，`version()` 由 entries 的稳定
JSON hash 派生。它实现 exact / structural / lexical 三个确定性检索档位；semantic
目前只作为 degraded fallback 标记，不伪装已有向量库。engine 已能消费
`codentum.resource-selection.v1` 中 `kind=knowledge` 的本地文件/目录，索引后把
`indexVersion` 与 memory ref 注入 ContextBundle。

`ModelGatewayRunner` 则读取同一份 Prompt Bundle，经冻结的 `ModelGateway` 发起一次模型调用，
并把模型响应、usage、tool_calls 与 prompt digest 写入 `model/` 证据目录。
百炼默认价格表来自 `codentum_harness.model_gateway.bailian_pricing`，以人民币/百万 Token
归因；缺价格或超出已审计价格阶梯时 fail-closed，不把真实调用记成 0 成本。

`TeamWorkerRuntime` 是 AgentTeams 适配器：从同一个 `SpawnRequest` 写 manifest /
checkpoint-0 / Prompt Bundle，通过官方 `agt` CLI 创建并检查 AgentTeams Worker 资源，
再把 Codentum task spec 交给可注入的 `AgentTeamsClient` 派发并回收结果。
创建/检查得到的 `status.json`、任务派发 `dispatch.json`、终局回收 `result.json`
都会同步投影成 Worker `progress` / `finished` 事件，因此桌面端现有执行中心可以直接展示
Team-mode 资源、派发和结果状态，不需要读取 AgentTeams 私有目录。

默认 `AgentTeamsDockerCLIClient` 负责资源管理和 Matrix Manager 派发：它从环境或
`~/agentteams-manager.env` 读取本机 AgentTeams 管理员凭据，向 Manager DM 房间发送包含
PromptBundle digest 的任务消息，并只在观察到 `CODENTUM_RESULT {...}` 终局标记时认定
完成或失败。缺凭据、Worker 未 Running、派发失败或超时未见终局标记都会 fail-closed，
避免把“Worker 资源已 Running”误报成“Codentum 任务已完成”。

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
