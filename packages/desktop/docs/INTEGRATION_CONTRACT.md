# Desktop Integration Contract（C 侧提案）

> 这是 C 侧适配边界，不修改 A 的冻结 contracts。A/B 接受前，所有未支持动作必须 capability-gated。

## 握手

```json
{
  "connected": true,
  "protocolVersion": 1,
  "engineVersion": "0.1.0",
  "runId": "run-20260808-001",
  "projectRoot": "C:\\work\\my-project",
  "stateRevision": 42,
  "capabilities": {
    "stateWatch": true,
    "requirements": false,
    "planConfirmation": false,
    "pauseAtSafePoint": false,
    "stop": true,
    "keepMemory": false,
    "forkFromCheckpoint": false,
    "appendPrompt": false,
    "insertModule": false
  }
}
```

连接成功的握手必须给出权威 `runId`；`project:<path hash>` 或 `fixture:*` 只是 C 侧状态源 ID，不能冒充运行编号。握手缺失、版本不兼容或没有 `runId` 时，命令按钮全部禁用，但只读项目视图仍可工作。fixture 永远只读，即使引擎已连接也不能发送命令。

## 命令信封

```ts
interface OperatorCommand {
  commandId: string
  runId: string
  expectedRevision: number
  target: { agentId: string; packetId?: string; moduleId?: string }
  action:
    | 'pause_at_safe_point'
    | 'resume'
    | 'stop'
    | 'stop_keep_memory'
    | 'fork_from_checkpoint'
    | 'append_prompt'
    | 'insert_module'
    | 'confirm_plan'
  payload: unknown
  requestedAt: string
}
```

`commandId` 用于幂等重试，`expectedRevision` 防止用户对过期页面操作。

## 回执状态机

```text
submitting → accepted → waiting_safe_point → applied
                  └──────────────────────→ rejected
submitting/accepted → timed_out（最终仍以权威状态重新同步）
```

前端只能在 `applied` 或包含相同 `commandId` 的权威状态更新后改变业务状态。

## A 侧 ContractChangeRequest

1. Agent/Worker 投影：id、role、state、currentPacket、currentModule、heartbeat、budget、7d success rate。
2. Schedule 投影：planned/actual start/end、criticalPath、split/rework、dependency arrows。
3. WIP 投影：每个 PacketState 的 limit/current。
4. Operator command/receipt/event Schema 与 revision 规则。
5. Requirement、clarification、DevelopmentPlan、Provisioning 的只读形状。
6. 将握手中的权威 `runId` 与已打开项目的 canonical identity 绑定；C 未能确认绑定一致时不得派单。

## 精益调度只读投影

C 已在“执行中心”提供 Agent 执行、流动看板、价值流和瓶颈诊断四个视图，不新增第二套任务看板，也不直接修改 Packet 状态。以下两个文件均为可选投影；文件不存在时 C 只显示已有 `PacketState`，并明确标注缺失能力。

### `.codentum/scheduling.json`（A 提供）

```json
{
  "schemaVersion": 1,
  "revision": 12,
  "updatedAt": "2026-08-14T10:00:00.000Z",
  "wipLimits": { "ready": 4, "running": 2, "review": 2 },
  "readyQueue": ["wp-ready-2", "wp-ready-1"],
  "criticalPath": ["wp-running"]
}
```

- `wipLimits` 只接受非负整数；未提供的状态显示 `—`，C 不推算默认值。
- `readyQueue` 是 A 的确定性拉式队列；C 只用它排序 `ready` 列。
- `criticalPath` 是 A 的计划结果；C 不根据前端依赖图重复计算。

### `.codentum/flow.json`（A 汇总，B 提供原始 Worker 事件）

```json
{
  "schemaVersion": 1,
  "calculatedAt": "2026-08-14T10:01:00.000Z",
  "efficiency": 0.42,
  "stages": [{ "state": "review", "packetCount": 3, "waitP80Ms": 64000 }],
  "packets": [{
    "packetId": "wp-running",
    "totalCycleMs": 90000,
    "efficiency": 0.42,
    "segments": [{ "state": "running", "kind": "value", "durationMs": 38000 }]
  }],
  "bottleneck": {
    "state": "review",
    "waitP80Ms": 64000,
    "affectedPackets": 3,
    "recommendation": "优先处理评审队列。"
  },
  "andons": [{
    "id": "andon-1",
    "packetId": "wp-running",
    "severity": "warning",
    "reason": "连续等待超过阈值。",
    "at": "2026-08-14T10:01:00.000Z"
  }]
}
```

- A 负责从权威 Packet 状态变更与 B 的 Worker 事件计算等待/增值区间、p80、流动效率、瓶颈和安灯结果；C 不使用文件修改时间推算。
- B 需要在 Worker 事件中稳定提供 `packetId`、事件时间、模块/阶段与状态变化，使 A 能完成确定性汇总。
- 文件必须原子替换并通过结构校验；坏 JSON 或非法字段会被拒绝，不会覆盖上一份一致快照。
- 拖拽调度、修改 WIP、解除安灯等写操作仍需 A 提供正式 command/capability、revision 和回执状态机。在此之前 C 保持只读。

## 需求草稿与本地附件

- 需求草稿和附件选择是 C 的本地能力，不依赖 A/B 引擎连接；支持从电脑任意位置选择任意文件类型或整个文件夹。
- 只接受普通文件与目录，递归拒绝符号链接、junction 和特殊文件；单文件上限 512 MiB，单草稿合计上限 1 GiB，最多 20 个顶层附件。单文件夹最多 10,000 个文件、10,000 个目录、128 层。选择时记录原始绝对路径并计算文件或目录树 SHA-256，不复制源内容；源内容在校验期间变化则整项回滚。
- 附件 manifest 保存在 Electron `userData/requirement-drafts`，使用随机引用 ID 与仅当前用户可读的创建权限并原子替换。切换项目或重启应用后仍可恢复引用；用户移动、删除或修改源文件后，提交前的重新校验会拒绝失效引用。
- Renderer 只接收 `{ id, name, kind, fileCount, sizeBytes, sha256 }`，不接收源路径、附件绝对路径、文件内容或 base64。
- `submit_requirement` 的 `payload.attachments` 在 Renderer 侧仍只有上述元数据。Electron 主进程在发送前重新校验所有权、大小和 SHA-256，随后才给 sidecar 增加 `{ localPath }`；这一路径指向用户选择的原始文件或目录，不创建私有副本。
- 引擎只有在握手 `projectRoot` 与当前项目 canonical identity 一致时才能接收命令。A/B 在读取路径、形成上下文和返回回执前必须保持 `requirements=false`。
- 切换项目时草稿按状态源隔离；从未绑定状态携带到首次真实项目的草稿通过原子 manifest 更新迁移。未派单草稿不会写入 `.codentum`，也不会伪造对话消息。

## 工作区绑定与首次初始化

- **警告是什么**：`[missing] State directory is unavailable` 表示所选工作区里没有 `.codentum` 运行状态目录；它不是“文件夹无法打开”或“路径不可读取”。
- **为什么会有**：桌面端已经绑定并读取了工作区，但当前桌面会话尚未连接并运行真实 engine，因此这个普通文件夹还没有生成 `graph.json`、`budget.json`、任务与证据目录。
- **怎么解决**：以当前 canonical 工作区启动 `packages/engine` 的真实 JSONL CLI，并完成握手；引擎在真实 `submit_requirement` 时原子初始化 `.codentum`，C 监听状态并自动刷新。只隐藏警告或由 C 手工伪造 JSON 都不算解决。
- 打开任意文件或文件夹后，C 规范化真实项目根路径，关闭旧 Sidecar 会话，并以该路径作为新 Sidecar 进程的 `cwd`；同时通过 `CODENTUM_PROJECT_ROOT` 显式传递路径。
- 真实引擎连接后，握手返回的 canonical `projectRoot` 必须与所选工作区一致，否则 C 立即关闭会话并保持所有执行能力禁用。
- 工作区不需要预先包含 `.codentum`。目录缺失表示运行状态尚未初始化，不表示文件夹打开失败；此时仍允许编辑需求、添加附件和保存本地任务草稿。
- `.codentum` 必须由真实 engine/控制平面流程原子创建，最小完整形状为 `graph.json`、`budget.json`、`decisions.jsonl`、`packets/` 和 `evidence/`。C 不创建或伪造控制平面状态。
- 当前真实 `submit_requirement` 已能把需求送至真模型并写入权威状态，但已留存样本的 `tool_calls` 为空、没有项目文件被创建。C 可以证明路径绑定和状态刷新，不能宣称 Agent 已修改普通文件。

## B 侧能力请求

1. 可持续订阅的 Worker 事件流。
2. 安全点暂停与 checkpoint 持久化完成事件。
3. checkpoint 恢复/派生分支。
4. Prompt 队列与上下文注入回执。
5. 动态模块变更的执行语义。
6. 可打包的 Python CLI/sidecar 入口。

当前 `WorkerRuntime` 的 `abort` 可映射为 stop；`resume` 仍未实现，不能宣称返回/恢复可用。

### C 侧界面门控

- `fork_from_checkpoint` 在 Worker 投影没有权威 checkpoint 标识前不显示，不能用“上一个模块”代替 checkpoint。
- `resume` 只有在最新 Worker 事件明确给出 `state/status=paused` 且引擎开放能力时显示；通用 `waiting` 不等同于已暂停。
- `append_prompt` 与 `insert_module` 不向 completed/failed/aborted Worker 显示。
- 依赖图引用但 `packets/` 中缺失的节点显示为 `missing`，不伪装成 pending WorkPacket。

## 当前 A/B 接入审计

- C 已读取所选项目的冻结 `.codentum` 状态，并安全枚举该 Git 仓库登记的隔离 worktree；只合并
  `evidence/<worker>/manifest.json` 与 `events.jsonl`，不把 worktree 内的状态副本当主项目真源。
- B 当前事件只保证 `started/checkpoint/finished`，没有固定 `moduleId` 序列；C 因此只显示实际出现的模块，
  不补预设动画步骤。
- `packages/engine` 现已提供 `python -m codentum_engine --project-root ...` JSONL composition root，且
  `SidecarGateway → engine → ReconcileLoop → LocalWorkerRuntime → 真模型` 已有一次真实运行证据。
- 该真实样本仍是 one-shot 模型调用：`tool_calls` 为空，没有创建项目文件；AgentTeams Team runtime shell
  与本地 Worker 资源已有 smoke，但尚不能描述为桌面端 Team-mode 完整闭环。
- engine 尚未构建为随 Windows 安装包分发的独立产物。系统没有 Python、源码和开发依赖时的冷启动
  仍未验证，因此不能把 Python CLI 的可运行性写成安装交付完成。
