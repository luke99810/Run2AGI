# Codentum 桌面端（C）深度评估报告

> 审查日期：2026-08-05 | 审查人：A（队长）| 依据：00-总体设计方案 + 02-实施路线 + Demo 剧本

---

## 一、产品定位的根本性误解

设计文档第一句：

> "你输入一个软件需求，它编排一支 Agent 团队完成开发，全过程在可视化界面中可见，
> 最终一次性收集凭证与素材，做生产级测试并打包交付。"

这是一个**软件开发自动化平台**——用户说需求 → Agent 开发 → 交付软件。

C 当前的理解是**"行业方案中心"**——用户选行业 → 看预配置方案。IndustryWorkspace
硬编码了 4 个虚构行业（运维/研发/金融/客服），配有 `Math.sin()` 模拟的假指标
动画。这 547 行代码、315 行虚构数据、以及相关测试，与设计文档零关联。

设计文档中的 11 个 Agent 角色（intake / architect / planner / qa / coder / 
helper / reviewer / integrator / manager / evolver / guardian）在 IndustryWorkspace 
中完全不存在。行业方案里的 algorithm profiles（scheduler/optimization/security 等）
与 RoleSpec 也没有任何对应关系。这说明 C 读了 types 但没读设计。

---

## 二、设计要求的 UI 模块 vs 实际交付

### 设计规定的 17 个 UI 模块（来源：00-总体设计方案 §可视化控制台）

**观测类（8 个）——"看系统在干什么"**
| # | 设计规定 | 当前状态 |
|---|---|---|
| 1 | 甘特图（主视图）——计划vs实际双条，关键路径高亮，依赖箭头 | ❌ 未实现。OperationsDashboard 里有一个用纯 CSS 模拟的假甘特图，startPercent/widthPercent 硬编码 |
| 2 | 看板——按状态列，WIP 上限标红 | ⚠ KanbanBoard 能展示 8 列，但缺 WIP 上限、缺拖拽、纯只读 |
| 3 | Agent 状态板——每个 Worker 状态/预算条/7d 成功率 | ⚠ OperationsDashboard 的 agents 子视图有基础展示，但预算条未实现，成功率未实现 |
| 4 | 依赖图——DAG 可视化，节点按状态着色 | ❌ OperationsDashboard 里有一个假依赖图，节点坐标硬编码 |
| 5 | 绿线时间轴——main 健康状态 | ❌ 未实现 |
| 6 | 预算仪表——实时消耗，按阶段/角色/模型下钻 | ⚠ OperationsDashboard 的 cost 子视图从 budget.json 读数据，方向对，但 budget.json 目前 A 未写入 |
| 7 | 溯源查询——输入需求反查一切 | ❌ 未实现 |
| 8 | 进化曲线——holdout pass@1，实验 vs 对照 | ❌ 未实现 |

**交互类（4 个）——"人的四个动作"**
| # | 设计规定 | 当前状态 |
|---|---|---|
| 9 | 开发计划书确认页——must_have/wont_have/成本/并行度/开始按钮 | ❌ 完全缺失。这是 Demo 第一个高潮镜头，用户打开应用后第一眼就该看到的东西 |
| 10 | 批量决策收件箱——升级批次四要素 | ❌ 未实现 |
| 11 | 审批卡片——R3+ 人工审批 | ❌ 未实现 |
| 12 | 配置收集表单——Provisioning（API Key/SMTP/Logo/域名） | ❌ provisioning/ 目录为空。这是 Demo 第三个高潮镜头 |

**管理类（4 个）——"配系统能干什么"**
| # | 设计规定 | 当前状态 |
|---|---|---|
| 13 | Skills 面板——按作用域分组/状态/版本/token开销/命中率/自进化管理 | ❌ panels/ 目录为空 |
| 14 | MCP 面板——已连接Server/工具数/调用量/失败率/角色授权 | ❌ 未实现 |
| 15 | 连接器面板——GitHub/GitLab/Slack/云服务绑定+权限最小化提示 | ❌ 未实现 |
| 16 | 模型与成本面板——路由表编辑器/成本仪表/预算策略/Provider切换 | ❌ 未实现 |

**额外增加（设计中不存在）**
| # | 实现内容 | 问题 |
|---|---|---|
| 17 | IndustryWorkspace（方案中心）547 行 | 设计文档中完全不存在。虚构行业方案展示，与产品目标零关联。应删除。 |
| 18 | ExecutionControlDrawer 255 行 | 设计中没有这个组件。pause/resume/stop 命令全部是前端本地状态变更，从未调用后端。UI 存在但功能是假的。 |

---

## 三、对照 Demo 剧本逐镜头检查

Demo 剧本（02-实施路线 §Demo 剧本）定义了 3 分钟录屏的 6 个镜头。
每个镜头都有明确的画面要求和真实产物。

| 镜头 | 剧本要求 | 当前能实现？ |
|---|---|---|
| [0:00] 说需求 | "桌面应用主界面，输入'做一个管理 SaaS 订阅的记账工具'，系统开始提问" | ❌ 没有输入框 |
| [0:20] 确认计划书 | "完整计划书——3 条 must_have / 4 条不做 / 5 模块 / 并行度 3 / 预计 $8.4 / 后期需 Key+Logo" | ❌ 完全没有 |
| [0:45] 并行开发 | "甘特图三条并行推进 + 看板各列流动 + Agent 状态板预算条 + locks.json 特写" | ❌ 无法展示真实执行 |
| [1:20] 约束结构性 | "Coder 改 contracts → 文件系统写入失败 → 提交 CCR → Architect 评估" | ❌ 无法展示 |
| [1:50] 一次性填表 | "配置收集表单——汇率 API Key / SMTP / Logo / 域名 → 连通性校验" | ❌ provisioning/ 为空 |
| [2:20] 交付 | "E2E ✓ → 凭证泄漏扫描 ✓ → 打包 → 冷启动复现测试 ✓ → 交付报告" | ❌ delivery/ 只有 README |

**6 个 Demo 镜头，当前 0 个能拍。**

---

## 四、精力分配分析

```
12,583 行代码的实际去向：
├── 行业方案展示（IndustryWorkspace + profiles）    ~3,500 行  设计中不存在，应删除
├── CSS（大量看起来像 UI 框架自带）                  ~5,000 行  大部分非自写
├── 执行控制模型（前端本地状态机）                    ~1,200 行  未接后端，功能是假的
├── Agent 状态/看板（数据展示）                      ~2,500 行  方向部分正确
├── PythonEngineClient（通信层）                      ~450 行  设计良好但未接 UI
├── Electron 壳 + IPC                                  ~300 行  正确
└── 其他                                              ~633 行

有效产出（方向正确 + 功能真实）：约 3,250 行（26%）
无关/虚假产出：约 9,333 行（74%）
```

---

## 五、具体问题清单

### 功能性问题

1. **没有需求输入入口**。用户打开应用后第一个画面是"方案中心"展示虚构行业，不是设计要求的"输入需求"。

2. **ExecutionControlDrawer 是假的**。255 行的 execution-control-model.ts 定义了一个完整的前端状态机（pause/resume/stop/back/append_prompt/insert_module），但所有操作只修改前端内存里的 JavaScript 对象，不调用任何 IPC、不发送任何网络请求、不接触任何 Python 进程。用户在界面上点"暂停"，Worker 继续跑。

3. **PythonEngineClient 孤立存在**。449 行的通信层能 spawn Python 进程、收发 JSON-RPC，含完整测试，但没有任何视图引用它。grep 整个 views/ 目录，PythonEngineClient 导入次数为 0。

4. **交付链路空壳**。provisioning/、secret_scan/、cold_start/ 三个目录全是 .gitkeep。packaging/ 里只有一个 README 和一个 python_engine_probe.py。docker/ 三个子目录全是 .gitkeep。

### 设计偏离问题

5. "方案中心"（IndustryWorkspace）在设计文档中不存在。00-总体设计方案 §可视化控制台列出了 9 个视图 + 4 个人类交互界面 + 4 个管理模块，没有任何一个叫"方案中心"或"行业方案"。

6. 甘特图是设计规定的**主视图**——"横轴时间、纵轴 packet、计划 vs 实际双条并排、关键路径高亮、依赖箭头"。当前实现是一个用纯 CSS 模拟的假甘特图。

7. 设计规定 Console 的写入动作走"人类输入通道 → 变更请求 → 控制平面校验"，但 ExecutionControlDrawer 的设计完全绕过了这一架构，直接操作前端状态。

8. panels/ 目录在设计中被定义为四个管理模块（Skills / MCP / 连接器 / 模型与成本），全部为空。

### 架构问题

9. **无实时数据**。GoldenStateSource 只读一次快照，没有轮询、没有 watch、没有 WebSocket。用户看到的永远是启动时加载的那一帧静态数据。

10. **StateSource 接口只有 list() 和 read()，没有 subscribe() 或 watch()**。这意味着即使后端写入了新的状态文件，UI 也不会自动更新。

---

## 六、可保留的部分

| 组件 | 评价 | 处理 |
|---|---|---|
| Electron 壳（shell/main/index.ts） | 结构正确，窗口管理、preload、安全配置到位 | 保留 |
| StateSource 抽象（data/state-source.ts） | 接口设计合理，golden-state 和 project 双源 | 保留，加 subscribe() |
| GoldenStateSource | 正确读取 fixtures | 保留，后续加 ProjectStateSource |
| KanbanBoard（views/KanbanBoard.tsx） | 看板渲染正确，状态列映射正确 | 保留，加 WIP 上限、拖拽审批 |
| PythonEngineClient | 通信层设计良好 | 保留，接入 UI |
| kanban-model / operations-model | 数据模型正确 | 保留 |
| 测试文件 | 覆盖到位 | 保留 |

---

## 七、修正方案

### 第一步：砍（2 小时）
- 删除 views/IndustryWorkspace.tsx
- 删除 views/industry-profiles.ts
- 删除 views/industry-profiles.test.ts
- 删除 views/execution-control-model.ts（保留类型定义，重写交互逻辑）
- 删除 views/ExecutionControlDrawer.tsx（用新设计替换）
- HomeView 移除"方案中心"Tab

### 第二步：补核心入口（1 天）
- 新建 views/RequirementInput.tsx：需求输入框 + Intake 追问 + 确认计划书
- 新建 views/PlanConfirmation.tsx：must_have/wont_have/成本/并行度/开始按钮
- 对应 Demo 镜头 [0:00] 和 [0:20]

### 第三步：接真实后端（2 天）
- PythonEngineClient 通过 IPC channel 接入 renderer
- ExecutionControlDrawer 重写：命令通过 IPC → PythonEngineClient → reconcile loop
- 新建 preload API：暴露 spawn/pause/resume/stop/sendPrompt 等方法
- 新建 ProjectStateSource：读真实 .codentum/ 文件，定时轮询或 fs.watch

### 第四步：补交付链路（2 天）
- provisioning/：凭证收集表单 UI + 本地加密存储
- secret-scan/：扫描集成
- docker/cold-start/：容器定义

### 第五步：补管理面板（P1，初赛后）
- Skills 面板、MCP 面板、连接器面板、模型与成本面板

---

## 八、修正后的目标 UI 结构

```
HomeView
├── Tab 1: 新任务
│   ├── RequirementInput（需求输入 + Intake 追问）
│   └── PlanConfirmation（计划书确认 + 开始按钮）
│
├── Tab 2: 实时看板
│   └── KanbanBoard（8 列 + WIP 上限 + 点击打开执行控制）
│       └── ExecutionControlDrawer（已接 PythonEngineClient）
│
├── Tab 3: Agent 状态
│   └── OperationsDashboard（甘特图/依赖图/成本仪表/Agent 列表）
│
├── Tab 4: 交付
│   └── DeliveryPanel（Provisioning/打包/冷启动结果）
│
└── Tab 5-8（P1）: Skills / MCP / 连接器 / 模型与成本
```

---

## 九、总结

C 的代码量是三人中最多的（12,583 行），但约 74% 的代码投入在了设计文档不要求的方向上。
设计规定的 17 个 UI 模块中，C 完成了约 2 个（看板 + Agent 状态的子集），增加了 2 个
设计中不存在的模块（方案中心 + 假执行控制）。

核心问题不是技术能力——Electron 壳、TypeScript 类型、PythonEngineClient 都写得很规范——
而是**没有按照设计文档开发**。Index.ts 的注释写的是对的（"不依赖 control-plane，只依赖
contracts + fixtures"），但执行时变成了"只做数据展示 Demo，不做任何真实交互"。

当前代码中约 3,250 行可以保留，其余需要重写或删除。预计修正工作量 3-5 天。
