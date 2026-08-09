# Codentum 桌面端设计与代码知识库

> owner: C · status: active · authority: `docs/00` → `docs/01` → `docs/02` → `docs/03` → ADR

## 1. 产品与角色 C

Codentum 是本地优先的软件开发 Agent 工作台。用户提交需求，系统完成澄清、计划、并行开发、配置收集、测试与打包。桌面端是产品入口，同时保持故障隔离：状态投影是只读的，人的操作经独立命令通道交给控制平面校验。

C 只写：

- `packages/desktop/**`
- `packages/delivery/**`

C 不修改 `contracts`、`control-plane`、`harness`、`roles`、`fixtures` 或根目录 `docs`。缺契约时向 A 提 ContractChangeRequest；缺执行语义时向 B 提接口请求。

## 2. Agent 团队

| Agent | 负责 | 明确不负责 |
|---|---|---|
| Intake | 澄清需求、ProductBrief、验收场景、假设 | 代码、隐式脑补 |
| Architect | 技术选型、契约、边界、ADR、可实现性探测 | 业务实现 |
| Planner | WorkPacket、依赖图、路径分配、虚拟调度 | 契约和代码 |
| QA | 先于实现写验收、测试矩阵、门禁核验 | 产品实现、发布 |
| Coder ×N | 在授权路径内实现、自测、提交、求助 | 契约、验收标准、main、自评审 |
| Helper | 全库只读检索与诊断 | 任何写操作 |
| Reviewer | 对抗评审 diff、契约和测试 | 改代码、合入、读取 Coder 私有推理 |
| Integrator | 合入、Tag、部署、回滚、交付 | 产品代码、跳过门禁 |
| Manager | 状态机、锁、调度、仲裁、升级 | 代码、评审、生产工具 |
| Evolver | Trace 聚类、提案、证伪、影子回放 | 直接合入、修改不动点 |
| Guardian | 无模型的风险定级、审批触发、硬拒绝、审计 | 语义判断、被 Agent 修改 |

MVP 可把 Intake+Architect 合为 Designer，把 Planner+Manager 合为 Coordinator；UI 仍使用权威 `RoleId`，不硬编码旧 PPT 的七角色编制。

## 3. 信息架构

桌面端采用工作台布局，不采用 PPT 式卡片墙：

```text
左侧：项目与 Agent 运行进度
中间：新任务 / 执行中心 / 看板 / 计划 / 依赖 / 成本 / 交付
右侧：当前模块详情与后端明确允许的操作
底部：持续存在的 Prompt 输入（仅在命令能力可用时提交）
```

第一屏必须是需求入口，而不是行业方案或虚构指标。常用路径：

```text
说需求 → Intake 追问 → 确认计划书 → 看执行 → 一次性 Provisioning → 交付
```

用户明确不希望看到“边界”“证据”作为顶级页面。内部仍保留审计数据，在界面使用“运行记录”“测试结果”“交付结果”等用户语言。

## 4. 执行模块交互

每个 Agent 的执行被投影成稳定模块序列。模块可在执行前选中，不操作则按默认计划执行。操作不能在 React 内存中假完成：

```text
点击模块
  → Renderer 调受限 preload API
  → Electron main 校验命令
  → Python sidecar / 控制平面
  → accepted 或 rejected 回执
  → Harness 在安全点实际执行
  → applied 事件或新权威快照
  → UI 才显示已暂停/已停止/已回退
```

动作语义：

- 暂停：等待下一个安全检查点；先显示“等待安全点”，不能立即显示已暂停。
- 停止并保留记忆：只有 checkpoint 持久化完成后才算成功。
- 返回上一步：从旧 checkpoint 派生新 attempt/分支，不删除历史。
- 追加 Prompt：写入当前执行上下文；运行中排队，暂停时在恢复前注入。
- 插入模块：提交 PlanChangeRequest，由 Planner/Manager 校验 DAG、路径、预算和 revision。
- 后端不支持：隐藏或禁用并显示后端返回的原因，不提供装饰性按钮。

## 5. 状态来源

`StateSource` 与 `CommandGateway` 必须分离：

- `FixtureStateSource`：显式标注快照模式，用于 empty/mid-flight/blocked 测试。
- `ProjectStateSource`：读取用户选择项目的 `.codentum/`，监听变更并发出 snapshot。
- Renderer 不读取文件系统；所有路径访问在 main 进程。
- malformed、stale、partial-write 和大图必须有明确错误状态，禁止自动回退成“开发中”。
- 当前 A 契约没有 `agents.json`、schedule、WIP 或 operator command；相应页面必须标注为“任务投影/依赖波次”，直至 capability/contract 存在。

## 6. 设计来源

只借鉴模式，不复制产品：

- [OpenHands](https://github.com/OpenHands/OpenHands)：前端与 Agent Server 分离、会话和事件流。
- [LangGraph](https://github.com/langchain-ai/langgraph)：checkpoint、interrupt/resume、历史分支与 replay 语义。
- [AgentOps](https://github.com/AgentOps-AI/agentops)：session → agent → operation 时间线。
- [React Flow](https://github.com/xyflow/xyflow) + [Dagre](https://github.com/dagrejs/dagre)：可点击 DAG 与自动布局。
- [Dagu](https://github.com/dagu-org/dagu)：本地 DAG 运行历史与人工任务；GPLv3，仅作设计参考。
- [electron-builder](https://github.com/electron-userland/electron-builder)：NSIS 安装包和 `extraResources`。
- [PyInstaller](https://github.com/pyinstaller/pyinstaller)：Python sidecar 目录分发。

许可证策略：可直接依赖 MIT/Apache-2.0 组件；GPL 项目只看交互，不复制代码或资源。新增依赖必须在发行物中生成第三方许可证清单。

## 7. UI 设计系统

- 浅色为默认，信息密度适中；左侧导航约 250–280px。
- 正文至少 15px，页面标题 26–32px，状态/辅助文本至少 13px。
- 白底、浅灰工作区、少量青绿色作为主操作色；红色只用于阻断/失败。
- 动效只表现真实状态变化，不用随机数、`Math.sin()` 或循环动画伪造运行。
- 所有异步区域具备 loading / empty / offline / stale / malformed / denied 状态。
- 键盘可操作、焦点清晰、颜色之外仍有文字/图标状态提示。

## 8. 交付门禁

`Codentum-Setup.exe` 必须包含 Electron 应用与 Python sidecar。最终门禁：在没有 Node、Python、源码的干净 Windows 环境中安装、启动、完成 sidecar 握手、读取真实项目、卸载。若 A/B 尚未提供生产 engine，安装包可构建，但必须明确 `engine_unavailable`，不能声称完整 Agent 闭环可用。

