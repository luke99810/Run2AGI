# Codentum Codex 风格工作台任务书

## 目标

把桌面端收敛为灰白、低干扰的 Agent 工作台，并保证任务、草稿、附件和上下文选择都是真实数据，不用静态假状态代替执行层。

## C 负责的交付

1. 左侧工作台：新对话、任务、对话、插件、知识库、Skills、设置、帮助。
2. 任务隔离：每个任务使用独立 `taskId` 和草稿作用域；新建任务不覆盖历史任务。
3. 历史检索：本机保存任务标题、需求摘要、状态和更新时间；左侧与对话页均可搜索并恢复。
4. 输入框：左下角提供文件/文件夹、访问权限、知识库、Skills、插件选择；允许引用电脑任意位置、任意扩展名的普通文件及文件夹，不复制到 Codentum 路径；提交时重新校验原路径、类型、大小与 SHA-256 后把真实路径交给 Agent。
5. 挂起任务：输入框上方只显示权威状态中 `blocked` / `pending` 的任务，不伪造暂停状态。
6. 设置与帮助：默认权限设置、Agent 管理入口、本地客服诊断信息生成。
7. 命令接入：提交需求时在现有 `OperatorCommand.payload` 中附带当前任务与历史检索上下文。
8. 右侧主工作区最终视觉收敛（功能链路全部完成后执行）：以 Codex 对话页为参考，顶部只保留任务标题和少量视图操作，中间使用连续对话/执行记录，底部固定单一输入区；挂起任务放在输入区上方，附件、权限和变更统计贴近输入区，详细运行信息默认折叠。当前阶段保持现状，避免功能接入期间反复改版。
9. 聊天管理：任务标题旁提供“…”菜单；C 负责按关键词、需求内容和附件名称搜索本机任务，并通过受控主进程保存对话记录 Markdown。后续完整 Agent 消息与工具调用记录须由 A 提供权威事件流后再纳入导出。
10. 本地/联网模式：C 负责每任务模式控件、隔离保存和 `connectivityMode` payload；未接入时必须明确阻止联网提交，不显示为可用。

## 提交给中心 Agent 的字段

```json
{
  "taskId": "uuid",
  "draftScope": "project:<id>:task:<uuid>",
  "requestedAccessMode": "read_only | workspace_write | full_access",
  "connectivityMode": "local | online",
  "attachments": [],
  "pluginIds": [],
  "knowledgeIds": [],
  "skillIds": [],
  "relatedTaskIds": [],
  "taskHistory": [
    {
      "taskId": "uuid",
      "title": "历史任务标题",
      "summary": "历史需求摘要",
      "status": "draft | submitted",
      "updatedAt": "ISO-8601"
    }
  ]
}
```

这些字段利用现有开放 payload，不要求修改冻结的 contracts schema。

## A 需要配合的轻量改动

1. 在控制平面开放 `submit_requirement` 能力，并保留上述未知 payload 字段，不提前丢弃。
2. Intake 创建计划时把 `taskId` 写入运行关联信息，使同一项目的多任务不会串线。
3. 现有 `pause_at_safe_point` 产生 `waiting` Worker 投影时补充可读的暂停原因；桌面端据此区分“等待依赖”和“人工挂起”。
4. 权限判定仍以 RoleSpec、路径锁和 Guardian 为上限，绝不能直接信任 `full_access` 请求。
5. 接收并审计 `connectivityMode`；联网模式必须经过域名策略、凭证隔离、网络权限、限流和审计记录，再把授权后的工具面交给 Worker。

## B 需要配合的轻量改动

1. Intake/Manager Prompt Bundle 读取 `taskHistory`，提供按标题、摘要和 `taskId` 检索的上下文工具。
2. 将 `skillIds`、`pluginIds`、`knowledgeIds` 映射到现有 RoleSpec / ToolSurface / Context Broker；无法提供时返回明确拒绝原因。
3. WorkerRuntime 接收 `requestedAccessMode`，只允许在 RoleSpec 上限以内收紧或授权，不能绕过 Guardian。
4. AgentTeams / HiClaw：当前仅完成本机预检和百炼 Provider 验证，尚未执行官方安装与 Team Runtime 适配。拿到可用 Key 后再安装验证并实现 `WorkerRuntime` 适配；在此之前桌面端只标识本地 Worker 已接入。
5. 为联网模式提供真实搜索、浏览器和爬虫 ToolSurface，回传来源、时间、失败原因及用量；本地模式不得加载这些网络工具。文件变更数量与增删行统计也由 WorkerRuntime 真实采集后交给 A 汇总。
6. 将已在最新协作快照实现的 `SidecarManager.bindProject(projectRoot)` 推入共享分支；C 当前以能力探测方式兼容远程旧版本，有该方法时立即绑定，没有时保持未连接状态。

## 联调验收

1. 连续新建两个任务，各自添加不同附件；来回切换后草稿和附件不串。
2. 中心 Agent 能通过 `taskHistory` 找到前一个任务，并在日志中记录命中的 `taskId`。
3. 选择只读权限后，Worker 写文件被结构性阻止；选择完全访问也不能突破 RoleSpec 上限。
4. 未接入的插件或 Skill 返回“不可用/未配置”，桌面端不得显示为已启用。
5. Claw 未安装时只显示本地运行时；安装和 Team Runtime smoke 全绿后才允许显示 AgentTeams 已连接。
