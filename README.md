# Codentum

> **Code + Momentum** — 自进化多 Agent 软件开发系统
>
> Codentum 是一个本地优先的桌面软件：输入一份软件需求，系统自动编排一支专业化 Agent 团队完成完整的开发流程——从架构设计到编码、评审、测试，最终打包交付。全过程在可视化界面中实时呈现，所有状态变更附证据溯源。

---

## 问题：多 Agent 协作的可靠性从何而来

单个大模型已经能写出可用的函数。但让**多个**大模型协作完成一个软件的完整交付——架构、编码、评审、测试、打包——可靠性从何而来？

单一模型场景的失败，多半是「答得不好」；多 Agent 场景的失败，则会成倍放大：

- 两个 Agent 同时修改同一个文件，后写静默覆盖先写；
- 一个 Agent 把契约改成只对自己有利的形状，下游全线崩坏；
- 「验收通过」来自模型的自述，而非任何可执行的判据；
- 一轮失败重试一轮，成本在无人察觉中失控。

提示词能缓解这些问题，却**无法根除**：你可以要求模型「别改这个文件」「先读契约」，模型「可能」照做，但**无法保证**照做。凡是靠「劝」守住的位置，迟早会翻车。

---

## 核心命题

> **多 Agent 系统的可靠性来自不变量，不来自提示词。**

不是让模型更听话，而是让模型**想犯错也犯不了**——凡是依赖提示词约束行为的位置，都用结构性约束使其成为不可能。由此导出系统的确定性边界：

- **不可恢复的错误** → 确定性代码（零 LLM 调用）：状态机、路径锁、准入校验、门禁、预算、守卫。
- **可恢复的错误** → 模型重试：代码生成、评审、测试，打回重来即可。

这条边界是 AI Infra 的架构第一原则：**大模型是概率性的不可靠组件，基础设施的任务，是让由不可靠组件组成的系统变得可靠。** 做法不是给模型加更多提示词，而是把「必须确定」的部分从模型手里拿走，交给软件工程。

完整设计依据见 [`docs/00-总体设计方案.md`](docs/00-总体设计方案.md)。

---

## 核心架构

### 五平面分离

Codentum 采用控制/执行/数据/上下文/进化五平面架构，借鉴 Kubernetes 控制循环与声明式状态管理模式。

```text
控制平面  (Control Plane)     确定性代码，零 LLM
  └─ reconcile · state_machine · locks · admission · gates · budget · guardian

执行平面  (Execution Plane)    模型驱动，可重试
  └─ Intake → Architect → Planner → QA → Coder×N → Helper → Reviewer → Integrator → Manager → Evolver

数据平面  (Data Plane)         依赖图 · 所有权图 · 溯源图 · 知识图 · Git 仓库 · 三层记忆
上下文平面 (Context Plane)     可见性矩阵 · 配方 · 预算降级
进化平面  (Evolution Plane)    证伪门 · 影子回放 · Skill CI · 灰度发布
```

**同构关系**：控制平面 `reconcile` → K8s controller manager；`.codentum/` → etcd；`WorkPacket` → Pod spec。控制平面不 import 执行平面任何模块，WorkerRuntime 通过构造函数注入，确保执行平面可整体替换而控制平面零改动。

### 控制平面：确定性编排内核

控制平面是系统可靠性的根基，七个模块全部为纯 Python 确定性代码：

| 模块 | 职责 |
|---|---|
| `state_machine` | 状态转移表（从 RoleSpec 派生，不硬编码） |
| `locks` | 前缀树路径锁（线程安全 + 乐观版本锁，保证 I1 单写者） |
| `reconcile` | 调和循环，对标 K8s controller manager，幂等推进 |
| `admission` | 准入校验，拦截不合规 WorkPacket |
| `gates` | 门禁，验收时判定 packet 是否通过 |
| `budget` | 预算追踪，按角色/模型分摊成本 |
| `guardian` | 系统守卫，加载期强制 `usesModel=false` |

状态转换图（六条转换，全部由状态机规则驱动）：

```text
pending ──► ready ──► running ──► review ──► accepted
   │                     │           │
   │                     │           └──► rejected
   └──► blocked ◄────────┘                (可打回重试)
```

| 转换 | 判据 |
|---|---|
| `pending → ready` | 依赖的 packet 全部 accepted |
| `ready → running` | 获取 `ownsPaths` 的全部路径锁（I1 强制执行点） |
| `running → review` | Worker 执行结束（成功或失败均进入 review） |
| `review → accepted` | 门禁通过；无门禁时兜底要求至少一条非 sys: 证据且 worker 未失败 |

调和循环是**幂等**的：同一状态执行 N 次 tick，结果一致，副作用仅发生一次。进程崩溃后从 `.codentum/` 重新 `load_state()` 即可恢复。

### 执行平面：Agent 团队协作

执行平面采用 LangGraph 编排，通过三段式外壳（准备 → 模型推理 → 证据收敛）驱动 11 个专业化 Agent 角色协作。每个角色具有独立的：

- **RoleSpec**：角色规约，定义输入/输出契约
- **可见性矩阵**：控制该角色可访问的文件路径和上下文
- **Skill 清单**：角色专属能力模块
- **Prompt 模板**：任务提示词

Worker 在独立的 Git worktree 中执行，确保数据隔离（I3 契约冻结：Architect 写 contracts/，Coder 只读挂载）。WorkerRuntime 是控制平面与执行平面的唯一接口——控制平面通过 SpawnRequest 发起任务，通过 SettleRequest 收集证据，不接触任何模型调用细节。

#### 执行段的控制流图（ADR-0004）

三段式中的**执行段**由 LangGraph 图驱动，前后两段无模型参与：

```text
准备【无模型】  →  ★ LangGraph 图【有模型】  →  收敛【无模型】

              ┌──────────┐
    ┌────────►│  model   │◄──────────────┐
    │     ┌────┼────┐                    │
    │ tool_calls help  no calls          │
    │     ▼     ▼      ▼                 │
    │  ┌─────┐┌────┐┌────────┐          │
    └──┤tools││help││ verify │──────────┘  谓词不过 → 回推
       └─────┘└─┬──┘└───┬────┘
                └───────┴──►[END]
```

**为什么执行平面用 LangGraph 而控制平面不用**——这两个 Graph 不是同一个东西：

| | 是什么 | 例子 |
|---|---|---|
| 四张图 | **数据结构**——系统状态的拓扑 | 依赖图（谁等谁）、所有权图（哪条路径被谁锁） |
| LangGraph | **控制流图**——一次执行的步骤走向 | 节点是函数，边是转移 |

依赖图不是可执行的图，它是一张「谁要等谁」的表——**拿 LangGraph 表达它是范畴错误**。
而一次 worker 执行内部的「推理 → 用工具 → 自验 → 收敛」循环，正是控制流图。

控制平面不用它的三条理由：控制平面零 LLM；状态存 Git 文件而 checkpointer
需自有持久化，二者冲突；幂等与崩溃恢复由确定性代码保证更易论证。

**节点与路由分离**：四个节点由调用方注入，路由是纯函数（只读 state、无副作用）——
「下一步走哪」因此可脱离模型、文件系统、网络单独验证。
图的测试 1.25 秒跑完（`test_agent_graph.py`），端到端语义测试需 4 分钟
（`test_agent_runner.py`），两层都有。

### `.codentum/` 状态目录

控制平面与桌面端之间通过文件系统通信，无需 RPC 层。`.codentum/` 是唯一的共享接口——控制平面写入，桌面端只读。

```text
.codentum/
├── graph.json          必需  依赖图 + 所有权图（锁表投影）
├── packets/            必需  WorkPacket JSON，磁盘为唯一真源
├── budget.json         必需  预算账本，按角色/模型维度分摊
├── decisions.jsonl     必需  决策流水（空文件合法）
├── evidence/           必需  证据产出物目录（空目录合法）
├── knowledge/          可选  知识条目
└── roles/              可选  RoleSpec 覆盖
```

前五个组件缺一不可：桌面端加载时逐项校验，缺失任何一项判定快照不连贯（`coherent = false`）并在 UI 提示，而非静默渲染半份数据。写侧使用 Pydantic 校验，读侧使用手写守卫——二者无共同生成源，因此 `tests/e2e` 中设有跨语言用例监控此接缝。

---

## 六条不变量

以下六条构成系统可靠性骨架，角色、流程、工具均可替换，不变式不可破。

| ID | 名称 | 内容 | 强制执行机制 |
|---|---|---|---|
| **I1** | 单写者 | 任一路径同一时刻仅被一个 in_progress packet 拥有 | 所有权图 + 前缀树锁 + Git hook |
| **I2** | 验收可判定 | 每个 packet ≥1 条机器可判定的验收谓词 | 准入校验器（确定性） |
| **I3** | 契约冻结 | 仅 Architect 可写 `contracts/`，Coder 只读 | 只读挂载 + hook + Guardian |
| **I4** | 绿线 | main 分支任何时刻可构建、可部署、E2E 通过 | 合入前在最新 main 重跑验收 |
| **I5** | 单会话闭合 | 一个 packet 必须在单会话预算内完成 | 预算计数器 + 自动 Splitter |
| **I6** | 证据 | 状态推进必须附证据引用，声明不算 | 状态机转换校验 |

**约束实现优先级**：不可见 > 无权限 > 被拦截 > 提示词劝阻。

> **I6 完整语义**：「执行完成但证据未落盘 = 未执行」。控制平面自身的簿记流水（锁获取、worker 失败等）带 `sys:` 前缀，验收时一律排除——系统不得以自身记录作为验收依据。

---

## 能力分层：Agent / Skill / Tool / MCP

### 分层

```text
Agent   身份 · 职责边界 · 权限集 · 模型路由        谁来做
 ├─ Skill  可复用能力包（判断规则 + 流程 + 校验）   怎么做   ← 共享
 ├─ Tool   原子动作（read / write / exec）        做什么
 └─ MCP    外部系统连接协议                       连到哪   ← 主 Agent 接一次
```

**Skill 是共享的**（跨角色、跨项目）；**MCP 与第三方应用由主 Agent 接入一次**，其工具自动进入工具面。新增一个第三方应用 = 加一个 JSON，**不改任何代码**。

### 13 个内置 Skill

`architecture` · `backend` · `cost-governance` · `debugging` · `delivery` · `evolution` · `frontend` · `integration` · `planning` · `requirements` · `review` · `security` · `testing`

每个 Skill 由 `SKILL.md`（决策规则）+ `manifest.json`（权限、失败策略、复用声明）构成：

```jsonc
{
  "id": "frontend", "scope": "role", "appliesTo": ["coder", "helper"],
  "inputs": {…}, "outputs": {…}, "preconditions": […],
  "failure": { "timeoutSeconds": 180, "silentDegrade": false },   // ★ 禁止静默降级
  "permissions": {
    "riskLevel": "R1",
    "tools": ["read_file", "write_file", "run_tests", "create_diff"],
    "writePaths": ["packages/desktop/**"]                          // ★ 最小权限
  },
  "reuse": { "crossRole": true, "crossProject": true }
}
```

**复用率是架构健康度指标**：`testing` 被 4 个角色引用、`review` 被 3 个引用。若每个角色各有一套专属 Skill，说明抽象失败。

### 第三方应用（使用者自配凭据）

| id | 用途 | 需要的凭据 |
|---|---|---|
| `playwright` | 端到端测试、浏览器自动化 | **无需凭据** |
| `github` | 仓库、Issue、PR、提交历史 | `GITHUB_PERSONAL_ACCESS_TOKEN` |
| `sentry` | 线上错误追踪 | `SENTRY_ACCESS_TOKEN` |
| `postgres` | 只读查询与 schema 检查 | `POSTGRES_CONNECTION_STRING` |
| `notion` | 需求文档、技术方案 | `NOTION_TOKEN` |
| `feishu` | 文档、消息、日程、审批 | `APP_ID` / `APP_SECRET` |

启用三步：申请凭据 → 设环境变量（或填配置 `env`）→ `enabled` 改 `true`。

**缺凭据时不会启动进程**，而是如实报出缺哪个变量：

```
✗ GitHub（github）：未配置凭据：GITHUB_PERSONAL_ACCESS_TOKEN
  （设为环境变量或写入配置的 env 字段）
```

三条约定：`tools` 字段**刻意留空**（由 server 在 `tools/list` 时提供，预列会在版本变化后变成谎报）；`status` 如实写 `disconnected`；工具名带 `id` 前缀（GitHub 与 GitLab 都有 `create_issue`，不加前缀会静默覆盖）。

详见 [`packages/roles/mcp/README.md`](packages/roles/mcp/README.md)。

---

## 技术栈

| 层次 | 技术选型 | 决策依据 |
|---|---|---|
| 核心引擎 | Python 3.11+ | ADR-0003 |
| 桌面端 | TypeScript + Electron + React | ADR-0003 |
| 契约定义 | JSON Schema → Pydantic (Python) + TypeScript 类型 (生成) | ADR-0003 |
| Agent 编排 | LangGraph（仅执行平面） | ADR-0004 |
| 类型门禁 | mypy strict + ruff + Pydantic v2 | ADR-0003 |
| 模型适配 | 阿里云百炼 (Qwen) 默认 · Anthropic 可切换 | ADR-0005 |
| 状态存储 | Git 仓库（文件真源）· SQLite（派生索引） | — |
| 任务隔离 | Git Worktree + 路径锁 | ADR-0001 |

---

## 快速开始

```bash
pip install -e ".[dev]"          # Python 核心引擎
cd packages/desktop && npm i     # 桌面端
```

最小可运行链路——控制平面驱动真实 worker 执行一个 WorkPacket：

```python
from codentum_control_plane.budget import BudgetTracker
from codentum_control_plane.reconcile import ReconcileLoop
from codentum_harness.runtime import (
    LocalWorkerRuntimeConfig, RunnerConfig, build_local_worker_runtime,
)

loop = ReconcileLoop(
    state_dir=str(project / ".codentum"),
    budget_tracker=BudgetTracker(limit_cny=20.0),
)
loop.load_state()
loop.worker_runtime = build_local_worker_runtime(
    LocalWorkerRuntimeConfig(
        repo_root=project,
        runner=RunnerConfig.command_runner([...], timeout_seconds=900),
    )
)
loop.run_until_stable(max_ticks=30)
loop.save_state()
```

**装配要点**：

| 要点 | 缺失后果 |
|---|---|
| `runner` 必须显式配置 | 默认 `None` 下 worker 会构建 worktree 并写出 prompt bundle，但以 `runtime_error: no worker runner configured` 结束——任何 packet 无法被真实执行 |
| `repo_root` 必须是 git 仓库 | worktree 隔离依赖 `git rev-parse`；worker 工作区为 `<repo_root>/../codentum-workers/<pid>/attempt-N` |
| 建议配置 `budget_tracker` | 未配则不写 `budget.json`，状态目录不完整，桌面端判定不连贯 |
| 门禁组件默认关闭 | `gate_runner` / `guardian` / `transition_table` 默认 `None`，走兜底验收；生产环境须显式配置 |

完整示例见 [`tests/e2e/test_abc_integration.py`](tests/e2e/test_abc_integration.py) 中的 `TestRealExecution` 类，演示 pending → accepted 完整执行链路。

---

## 验证

```bash
make verify           # 全量验证
make verify-offline   # 零运行时依赖子集
```

| 命令 | 验证内容 | 可无依赖运行 |
|---|---|---|
| `make gen-check` | 生成物与 schema 一致性 | ✅ |
| `python scripts/validate_fixtures.py` | 固件过 schema + 八项交叉检查 | ✅ |
| `python scripts/check_boundaries.py` | 路径独占 · packages 全覆盖 · 契约冻结 | ✅ |
| `pytest tests/contract` | 契约用例（schema 自动生成） | ✅ |
| `python scripts/secret_scan.py` | 凭证扫描（工作区 + git 历史） | ✅ |
| `make typecheck` | mypy strict 五个 Python 包 | 需 `pip install -e ".[dev]"` |
| `make desktop-typecheck` | 桌面端 `tsc --noEmit` | 需 `npm i` |

前五项设计为零运行时依赖——在完整安装前即可验证契约自洽性。

### 固件八项交叉检查

JSON Schema 无法表达跨文件关系约束，以下检查确保核心不变式在固件层面成立：

```text
I1 路径不相交         running packet 的 ownsPaths 两两不重叠
验收制衡              acceptance.authoredBy ≠ role
锁持有者为 running    卡住即释放锁，防止单点阻塞整条流水线
依赖图无环            DAG 约束
图与文件一致          graph.json 节点 ↔ packets/ 文件
I6 审计链连续          断链视为篡改
证据引用有效           packet.evidence 指向的证据实体必须存在
无凭证泄露            固件中禁止真实密钥
```

---

## 生成物规约

以下文件由代码生成，任何人不得手写修改：

```text
packages/contracts/python/codentum_contracts/state.py   ← gen_types (Python)
packages/contracts/typescript/state.ts                 ← gen_types (TypeScript)
tests/contract/test_*.py                               ← gen_contract_tests
```

修改数据形状的标准流程：**修改 schema → `make gen` → 提交生成结果**。`make gen-check` 会拦截反向操作。手写两份相同逻辑将导致静默漂移——Python 端的解析与 TypeScript 端的渲染将对同一字段产生歧义。

---

## 文档索引

| 文档 | 内容 |
|---|---|
| [`docs/00-总体设计方案.md`](docs/00-总体设计方案.md) | 权威设计依据，26 章完整方案 |
| [`docs/01-角色详细设计与Skill清单.md`](docs/01-角色详细设计与Skill清单.md) | 各角色 Harness/Loop/Graph 精密设计 |
| [`docs/02-实施路线与交付计划.md`](docs/02-实施路线与交付计划.md) | 分期路线、MVP 边界、Demo 剧本 |
| [`docs/03-团队分工与协作规范.md`](docs/03-团队分工与协作规范.md) | 协作规范 |
| [`docs/04-模型路由表.md`](docs/04-模型路由表.md) | 按错误成本分档的模型路由策略 |
| [`docs/adr/`](docs/adr/) | 架构决策记录（含已否决方案与理由） |

---

## 许可

Apache-2.0（见 [`LICENSE`](LICENSE)）。依赖披露见 `docs/` 对应文档。推送前须通过 `secret-scan` 验证。

---

*Codentum = Code + Momentum。动量 = 质量 × 速度，守恒于无外力条件——能力沉淀为质量，交付速度为速度，评测锚点确保已获得的能力不流失。*
