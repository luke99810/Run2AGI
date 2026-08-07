# Desktop Integration Contract（C 侧提案）

> 这是 C 侧适配边界，不修改 A 的冻结 contracts。A/B 接受前，所有未支持动作必须 capability-gated。

## 握手

```json
{
  "connected": true,
  "protocolVersion": 1,
  "engineVersion": "0.1.0",
  "runId": "run-20260808-001",
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

## 需求草稿与项目文件引用

- 需求草稿和项目文件选择是 C 的本地能力，不依赖 A/B 引擎连接。
- 文件选择只接受当前项目根目录内的普通文件，拒绝符号链接、越界路径、`.git`、`.codentum`、单文件超过 20 MiB、合计超过 50 MiB或超过 12 个文件。
- Renderer 只接收 `{ path, sizeBytes, sha256 }`，其中 `path` 是 POSIX 风格项目相对路径；不通过 IPC 传绝对路径、文件内容或 base64。
- `submit_requirement` 的 `payload.references` 使用同一结构。A/B 在确认 schema、项目绑定与读取策略前必须保持 `requirements=false`，C 不把本地引用显示成“Agent 已读取”。
- 切换项目时草稿按状态源隔离；未派单草稿不会写入 `.codentum`，也不会伪造对话消息。

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
- 当前 Python 工程没有 CLI、`--stdio` JSONL engine、`__main__` 或 `[project.scripts]`，C 的网关没有
  可启动的 A/B 进程产物。
- A 当前用短生命周期 `asyncio.run()` 分别调用 `spawn/settle`，B 又在 `spawn` 所在 event loop 创建后台
  task；该 loop 结束会取消任务。A 传入的 workspace 还是仓库根，而 B 明确要求外置 worktree。两处修复前，
  不能通过 C 侧适配器把它包装成可发布引擎。
