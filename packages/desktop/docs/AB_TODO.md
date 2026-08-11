# A/B 待做

> 更新日期：2026-08-12
> 范围：仅记录 C 前端已经准备的接入点，以及仍需 A/B 提供的真实运行时能力。本文件不修改冻结契约，也不把待实现能力描述成已接通。

## 当前已接通

| 能力 | A/B 权威来源 | C 当前行为 |
|---|---|---|
| 项目预算与成本 | `.codentum/budget.json` | 独立成本页面展示总额、角色、模型与告警 |
| WorkPacket | `.codentum/packets/*.json` | 展示状态、路径、验收、预算、路由、尝试次数、证据与来源 |
| 依赖与路径锁 | `.codentum/graph.json` | 展示 DAG、所有权锁、持有任务、取得时间与锁版本 |
| Worker 状态 | Worker `manifest.json`、`events.jsonl` | 展示模块、工作目录、成本、时间及事件 payload |
| 证据与决策 | `.codentum/evidence/*.json`、`decisions.jsonl` | 展示证据链、产物引用和追加式决策 |
| 知识与溯源图 | `.codentum/knowledge.json`、`.codentum/knowledge/*.json` | 只读展示关系，不冒充 RAG 检索结果 |
| 资源选择 | `submit_requirement.payload` | 发送 `codentum.resource-selection.v1` 的 Skills、知识库和插件选择 |

## A 需要提供

### 1. Resource Selection 的准入与状态投影

C 已随 `submit_requirement` 发送：

```json
{
  "resourceSelectionContract": "codentum.resource-selection.v1",
  "resourceSelections": [
    {
      "id": "managed:<uuid>",
      "kind": "knowledge|skill|plugin",
      "scope": "global|role|project",
      "roleId": "coder",
      "sourceKind": "file|folder|git_url",
      "localPath": "<absolute path>",
      "gitUrl": "https://..."
    }
  ]
}
```

A 需要：

- 校验资源作用域、项目绑定、访问权限与路径安全。
- 将资源选择交给 B 的 Context Broker、RoleSpec/ToolSurface 解析流程。
- 投影每个资源的权威状态：`registered`、`indexing`、`ready`、`rejected`、`missing`、`error`。
- 返回稳定的错误码和可展示原因；C 不从日志文本猜测状态。
- 不读取未被当前任务选择或不在授权范围内的本地路径。

### 2. MemoryIndex/RAG 状态出口

A 需要把 B 的检索结果转换为 C 可只读展示的权威投影，至少包含：

- `queryId`、`query`、`mode`、`scope`。
- `indexVersion`，用于可复现与 replay。
- 命中条目的 `ref`、来源、片段摘要、层级和排序。
- `degraded`、降级原因、字符预算和实际使用量。
- 最终注入开发 Agent 的引用列表。
- 索引构建状态、更新时间和失败原因。

没有 `indexVersion` 和命中引用时，C 必须继续显示“RAG 未连接”。

### 3. MCP 管理投影与命令

A 需要提供：

- MCP 服务列表、transport、作用域、健康状态和最后检查时间。
- 添加、更新、启用、停用、删除和重新检查命令。
- 命令幂等 ID、revision 校验和结构化回执。
- 凭证字段只能写入安全存储；不得经状态文件或 Renderer 回传明文。
- 服务最终可用工具必须经过 RoleSpec、ToolSurface 和 Guardian 收紧后再投影。

在这些字段和命令不存在时，C 的 MCP 页面保持真实空状态。

### 4. RoleSpec 与能力握手

A 需要：

- 将 B 的内置 RoleSpec 投影到当前项目，或在握手中提供等价只读角色清单。
- 为每项操作如实声明 capability；未实现能力必须为 `false`。
- 为暂停、恢复、停止、保留记忆、追加 Prompt 和插入模块提供结构化 command/receipt。
- 保证握手 `projectRoot` 与 C 当前 canonical 项目一致。

## B 需要提供

### 1. MemoryIndex 的真实实现

B 需要实现冻结的 `MemoryIndex`，而不是把上传文件全文直接加入 Prompt：

```text
exact -> structural -> lexical -> semantic
```

要求：

- 解析文件和目录、稳定分块、建立索引并生成 `indexVersion`。
- 同 query 与同 indexVersion 必须逐条返回相同结果。
- semantic 仅作为兜底，能用确定性检索时不升级到向量检索。
- 检索结果受 RoleSpec 可见性、任务作用域和字符预算约束。
- Context Broker 记录最终注入条目、被拒绝条目和降级原因。
- Agent 执行证据能够反查本次使用的索引版本与引用。

### 2. 自定义 Skills 与插件加载

B 需要：

- 消费 `codentum.resource-selection.v1`。
- 校验 Skill manifest、入口、依赖、工具权限和适用角色。
- 将已准入 Skill 合并进 RoleSpec/ToolSurface，而不是仅在 Prompt 中声明。
- 对插件和 Git 来源进行隔离加载，禁止 Renderer 直接执行资源代码。
- 产生加载成功、拒绝和运行失败的结构化事件供 A 投影。

### 3. Worker 与产物投影

B 已写入基础 Worker manifest/events，仍需稳定提供：

- 模块 ID、模块状态、失败原因和安全点状态。
- `spentCny`、workspace、开始/结束时间。
- prompt manifest、usage、tool calls、result 和 checkpoint 的安全引用。
- AgentTeams/Team 模式的成员、任务分派、状态和结果回收。

敏感 Prompt、模型原文和凭证不得未经筛选直接投影给 Renderer。

### 4. MCP ToolSurface 接入

B 需要：

- 根据 A 的服务配置建立 MCP 会话并执行健康检查。
- 枚举工具后按 RoleSpec 与 Guardian 策略裁剪。
- 将允许的工具加入 Worker ToolSurface。
- 产生连接、调用、拒绝、超时和失败的结构化事件。

## 联调完成条件

以下条件全部满足后，C 才能把对应模块标记为“已连接”：

1. 普通项目中选择一个知识文件，A 返回索引状态，B 建立索引并给出非空 `indexVersion`。
2. 开发 Agent 的一次执行证据能反查 query、命中引用、降级状态和最终注入条目。
3. 选择自定义 Skill 后，B 的真实 ToolSurface 出现对应能力；取消选择后不再加载。
4. MCP 服务能够通过 A 的命令启停，B 只向授权角色暴露裁剪后的工具。
5. 所有状态在项目切换、进程重启和 revision 冲突后仍能正确恢复或拒绝。
6. fixture、C 本地注册表或仅被 A 保存的未知 payload 都不得作为“运行时已接通”的证据。

## C 的保持规则

- C 只读 A/B 的权威状态，不自行写入或伪造 `.codentum` 运行状态。
- 未声明 capability 的按钮保持禁用。
- RAG 缺少索引版本或引用时显示“RAG 未连接”。
- MCP 缺少服务投影时显示空状态。
- 自定义资源已登记但尚未被 B 消费时显示“待 A/B 运行时接入”。
- 成本只放在独立成本页面，不嵌入研发团队页面。
