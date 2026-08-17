# Codentum

> **Code + Momentum** — 自进化多 Agent 软件开发系统。

Codentum 是一个本地优先的桌面软件：输入一份软件需求，系统自动编排一支专业化的 Agent 团队，完成从架构设计、编码、评审、测试到打包交付的完整流程。全过程在可视化界面中实时呈现，每一次状态变更都附有证据溯源。

> 开源地址：[GitHub](https://github.com/luke99810/Run2AGI) · [Gitee](https://gitee.com/Codentum/codentum)

---

## 为什么需要 Codentum

单个大模型已经能够生成可用的函数。但当**多个**模型协作交付一个完整软件时，可靠性问题会成倍放大：

- 两个 Agent 同时修改同一文件，后写静默覆盖先写；
- 一个 Agent 将契约修改为对自身有利的形状，下游全线失效；
- 「验收通过」来自模型的自述，而非任何可执行的判据；
- 失败后逐轮重试，成本在无人察觉中失控。

提示词可以缓解这些问题，但**无法根除**：要求模型「不要修改这个文件」「先读契约」，模型**可能**遵守，但**无法保证**遵守。凡是依赖提示词约束的行为，迟早会失效。

---

## 核心思想：可靠性来自不变量，不来自提示词

> **多 Agent 系统的可靠性来自不变量，不来自提示词。**

Codentum 的目标不是让模型更听话，而是让模型**在结构上无法犯错**——凡是依赖提示词约束行为的位置，都用结构性约束使其不可能发生。由此划分系统的确定性边界：

| 错误类型 | 处理方式 | 具体机制 |
|---|---|
| **不可恢复的错误** | 确定性代码（零 LLM 调用） | 状态机、路径锁、准入校验、门禁、预算、守卫 |
| **可恢复的错误** | 模型重试 | 代码生成、评审、测试，失败后重试即可 |

这条边界是 AI Infra 的架构第一原则：**大模型是概率性的不可靠组件，基础设施的任务是让由不可靠组件组成的系统变得可靠**。方法不是增加提示词，而是将「必须确定」的部分从模型手中收回，交给软件工程。



---

## 架构

### 五平面分离

Codentum 采用控制 / 执行 / 数据 / 上下文 / 进化五平面架构，借鉴 Kubernetes 的控制循环与声明式状态管理模式。

```text
控制平面  (Control Plane)     确定性代码，零 LLM
  └─ reconcile · state_machine · locks · admission · gates · budget · guardian

执行平面  (Execution Plane)    模型驱动，可重试
  └─ Intake → Architect → Planner → QA → Coder×N → Helper → Reviewer → Integrator → Manager → Evolver

数据平面  (Data Plane)         依赖图 · 所有权图 · 溯源图 · 知识图 · Git 仓库 · 三层记忆
上下文平面 (Context Plane)     可见性矩阵 · 配方 · 预算降级
进化平面  (Evolution Plane)    经验沉淀 L0→L1 · 判据影子档位 · 证伪门（L2→L3，暂缓）
```

> 进化平面已标注实现状态。`L2→L3 证伪门`暂缓的原因不是技术难度，而是**证据当前不可得**：一条经验的证伪门只能是「判据冻结下，带它运行与不带它运行出现显著差异」，而当前单 packet 完成率约 20–40%，噪声远大于信号。

**同构关系**：控制平面 `reconcile` → Kubernetes controller manager；`.codentum/` → etcd；`WorkPacket` → Pod spec。控制平面不 import 执行平面的任何模块，WorkerRuntime 通过构造函数注入——执行平面可整体替换而控制平面零改动。

### 控制平面：确定性编排内核

控制平面是可靠性的根基，七个模块全部为纯 Python 确定性代码：

| 模块 | 职责 |
|---|---|
| `state_machine` | 状态转移表（从 RoleSpec 派生，不硬编码） |
| `locks` | 前缀树路径锁（线程安全 + 乐观版本锁，保证 I1 单写者） |
| `reconcile` | 调和循环，对标 Kubernetes controller manager，幂等推进 |
| `admission` | 准入校验，拦截不合规的 WorkPacket |
| `gates` | 门禁，验收时判定 packet 是否通过 |
| `budget` | 预算追踪，按角色 / 模型分摊成本 |
| `guardian` | 系统守卫，加载期强制 `usesModel=false` |

状态转换（全部由状态机规则驱动）：

```text
pending ──► ready ──► running ──► review ──► accepted
   │                     │           │
   │                     │           └──► rejected
   └──► blocked ◄────────┘                （可打回重试）
```

| 转换 | 判据 |
|---|---|
| `pending → ready` | 依赖的 packet 全部 accepted |
| `ready → running` | 获取 `ownsPaths` 的全部路径锁（I1 强制执行点） |
| `running → review` | Worker 执行结束（成功或失败均进入 review） |
| `review → accepted` | 门禁通过；无门禁时兜底要求至少一条非 `sys:` 证据且 worker 未失败 |

调和循环是**幂等**的：同一状态执行 N 次 tick，结果一致，副作用仅发生一次。进程崩溃后从 `.codentum/` 重新 `load_state()` 即可恢复。

### 执行平面：Agent 团队协作

执行平面采用 LangGraph 编排，通过三段式外壳（准备 → 模型推理 → 证据收敛）驱动 11 个专业 Agent 角色协作。每个角色具有独立的：

- **RoleSpec**：角色规约，定义输入 / 输出契约
- **可见性矩阵**：控制该角色可访问的文件路径与上下文
- **Skill 清单**：角色专属能力模块
- **Prompt 模板**：任务提示词

Worker 在独立的 Git worktree 中执行，确保数据隔离（I3 契约冻结：Architect 写 `contracts/`，Coder 只读挂载）。WorkerRuntime 是控制平面与执行平面的唯一接口——控制平面通过 `SpawnRequest` 发起任务、通过 `SettleRequest` 收集证据，不接触任何模型调用细节。

执行段由 LangGraph 控制流图驱动（推理 → 工具调用 → 自验 → 收敛），前后两段无模型参与。控制平面不使用 LangGraph，原因有三：控制平面零 LLM；状态存于 Git 文件，与 checkpointer 的自有持久化冲突；幂等与崩溃恢复由确定性代码保证更易论证。

### `.codentum/` 状态目录

控制平面与桌面端通过文件系统通信，无需 RPC 层。`.codentum/` 是唯一的共享接口——控制平面写入，桌面端只读。

```text
.codentum/
├── graph.json          必需  依赖图 + 所有权图（锁表投影）
├── packets/            必需  WorkPacket JSON，磁盘为唯一真源
├── budget.json         必需  预算账本，按角色 / 模型分摊
├── decisions.jsonl     必需  决策流水（空文件合法）
├── evidence/           必需  证据产出物目录（空目录合法）
├── knowledge/          可选  知识条目
└── roles/              可选  RoleSpec 覆盖
```

前五个组件缺一不可：桌面端加载时逐项校验，缺失任何一项判定快照不连贯（`coherent = false`）并在 UI 提示，而非静默渲染半份数据。写侧使用 Pydantic 校验，读侧使用手写守卫——二者无共同生成源，因此 `tests/e2e` 中设有跨语言用例监控此接缝。

---

## 六条不变量

六条不变量构成系统的可靠性骨架。角色、流程、工具均可替换，不变量不可破坏。

| ID | 名称 | 内容 | 强制执行机制 |
|---|---|---|
| **I1** | 单写者 | 任一路径同一时刻仅被一个 in-progress packet 拥有 | 所有权图 + 前缀树锁 + Git hook |
| **I2** | 验收可判定 | 每个 packet ≥1 条机器可判定的验收谓词 | 准入校验器（确定性） |
| **I3** | 契约冻结 | 仅 Architect 可写 `contracts/`，Coder 只读 | 只读挂载 + hook + Guardian |
| **I4** | 绿线 | main 分支任何时刻可构建、可部署、E2E 通过 | 合入前在最新 main 重跑验收 |
| **I5** | 单会话闭合 | 一个 packet 必须在单会话预算内完成 | 预算计数器 + 自动 Splitter |
| **I6** | 证据 | 状态推进必须附证据引用，声明不算 | 状态机转换校验 |

**约束实现优先级**：不可见 > 无权限 > 被拦截 > 提示词劝阻。

> **I6 完整语义**：「执行完成但证据未落盘 = 未执行」。控制平面自身的簿记流水（锁获取、worker 失败等）带 `sys:` 前缀，验收时一律排除——系统不得以自身记录作为验收依据。

---

## 判据的因果检验

不变量与门禁解决了一半问题。另一半是：**谁来检验判据本身？** 编码缺陷会让某条测试变红；判据缺陷不会——因为缺的正是那条测试。

Codentum 用一组**因果算子**为判据缺陷制造信号。它们共享同一个方法：不检查判据「写得对不对」（那是模式匹配，模型总能绕过），而是**将被判定的对象移走，观察判据是否变红**。

### 验收侧：六层判据

| 层 | 修的是 | 手法 |
|---|---|
| 1–3 | 拿控制面簿记当证据 · 门禁同洞 · 声称完成但未改文件 | 检查是否存在 X |
| 4 | 改了文件，但未达到验收标准 | 实际执行谓词 |
| 5 | 达到验收标准，但**验收标准是空的** | `vacuity_check`：移走实现，测试必须变红 |
| 6 | 各模块测试全绿、集成谓词全绿，但**集成未覆盖某模块** | `composition_check`：逐模块桩化 |

第六层将因果检验从单点推广到组合，防的是多 Agent 并行开发最典型的失败——「各段都对、合起来不通」。桩必须**签名保真**（AST 掏空函数体，保留导入与模块级常量）：删除文件得到的 `ImportError` 虽能令测试失败，却失败在了错误的理由上。

### 判据侧：变异检验与生命周期

| 工具 | 回答的问题 |
|---|---|
| `scripts/mutate_judgements.py --mode=strong` | 这条判据**有没有人碰过**（整条摘除，测试必须变红） |
| `scripts/mutate_judgements.py --mode=weak` | 这条判据的**边界有没有被测准**（AST 改一格：`<=`→`<`、`and`→`or`、去掉 `not`、常量差一） |
| `scripts/judgement_ledger.py` | 判据资产负债表：档位 × 命中 × 变异 |
| `scripts/judgement_gaps.py` | 反复发生、却从未被任何判据拦截的失败 |

判据具有**生命周期**：`shadow`（评估、记录、不拦截）→ `enforcing`。晋级需同时满足两个条件：**在真实案例上命中过 ≥1 次**（否则与不存在不可区分）+ **变异检验能杀死它**（否则修改后无信号）。

> 默认档位是有意选为 `enforcing` 而非 `shadow`：默认 shadow 会让新规则静默失效；默认 enforcing 可能误拦，但那是显式的、可发现的。

变异脚本自身有三个控制点：**基线绿**、**至少一条被杀死**、**正对照（塞入一条空规则）必须存活**。缺少第三点，「全部被杀死」的结论与「杀死判定永远为真」在证据上不可区分。等价变异体连同判定理由记录于 `scripts/lib/equivalent_mutants.py`，每次运行都打印完整清单——隐藏的排除项等于没有排除项。



---

## 能力分层：Agent / Skill / Tool / MCP

```text
Agent   身份 · 职责边界 · 权限集 · 模型路由        谁来做
 ├─ Skill  可复用能力包（判断规则 + 流程 + 校验）   怎么做   ← 共享
 ├─ Tool   原子动作（read / write / exec）        做什么
 └─ MCP    外部系统连接协议                       连到哪   ← 主 Agent 接一次
```

**Skill 共享**（跨角色、跨项目）；**MCP 与第三方应用由主 Agent 接入一次**，其工具自动进入工具面。新增一个第三方应用 = 增加一个 JSON 配置，不改任何代码。

### 13 个内置 Skill

`architecture` · `backend` · `cost-governance` · `debugging` · `delivery` · `evolution` · `frontend` · `integration` · `planning` · `requirements` · `review` · `security` · `testing`

每个 Skill 由 `SKILL.md`（决策规则）+ `manifest.json`（权限、失败策略、复用声明）构成：

```jsonc
{
  "id": "frontend", "scope": "role", "appliesTo": ["coder", "helper"],
  "inputs": {…}, "outputs": {…}, "preconditions": […],
  "failure": { "timeoutSeconds": 180, "silentDegrade": false },   // 禁止静默降级
  "permissions": {
    "riskLevel": "R1",
    "tools": ["read_file", "write_file", "run_tests", "create_diff"],
    "writePaths": ["packages/desktop/**"]                          // 最小权限
  },
  "reuse": { "crossRole": true, "crossProject": true }
}
```

**复用率是架构健康度指标**：`testing` 被 4 个角色引用、`review` 被 3 个引用。若每个角色各有一套专属 Skill，说明抽象失败。

### 第三方应用

MCP 由引擎连接一次，工具进入主 Agent 的工具面，所有 packet 共享：

```bash
python -m codentum_engine --project-root <项目> --mcp-config-dir packages/roles/mcp
```

不指定 `--mcp-config-dir` 则完全不接 MCP，内置工具照常可用。runner 接收的是**已连接的工具箱**而非配置目录——「每个 packet 连接一次」（8 路并行 = 48 个 npx 进程）因此在结构上不可表达，。

**默认开启**

| id | 用途 | 凭据 | 实测 |
|---|---|---|
| `playwright` | 端到端测试、浏览器自动化 | 无需 | ✅ 连接成功，24 个工具 |

**默认关闭 · 需凭据**

| id | 用途 | 需要的凭据 |
|---|---|
| `github` | 仓库、Issue、PR、提交历史 | `GITHUB_PERSONAL_ACCESS_TOKEN` |
| `sentry` | 线上错误追踪 | `SENTRY_ACCESS_TOKEN` |
| `postgres` | 只读查询与 schema 检查 | `POSTGRES_CONNECTION_STRING` |
| `notion` | 需求文档、技术方案 | `NOTION_TOKEN` |
| `feishu` | 文档、消息、日程、审批 | `APP_ID` / `APP_SECRET` |

**默认关闭 · 会绕过本项目不变量**

| id | 风险 | 绕过的不变量 |
|---|---|
| `git` | **R3** | 控制平面的状态机与 worktree 隔离 |
| `filesystem` | R2 | 工作区边界（内置 `write_file` 的路径穿越检查） |
| `browser` | R1 | 无，但与 `playwright` 完全重叠 |

> `git` 能够 commit / merge / 切换分支，而并行 packet 的 worktree 隔离与状态转移都建立在「只有 ReconcileLoop 操作 git」这一前提上。启用它等于让 Agent 与控制平面同时操作版本库。每个配置均标注了 `riskLevel` 与 `bypasses`。

启用三步：申请凭据 → 设置环境变量（或填写配置 `env`）→ `enabled` 改为 `true`。缺凭据时不启动进程，而是如实报告缺少哪个变量。连接结果（含未加载条目及其原因）落盘于 `<state-dir>/mcp/connections.json`。详见 [`packages/roles/mcp/README.md`](packages/roles/mcp/README.md)。

---

## 技术栈

| 层次 | 技术选型 |
|---|---|
| 核心引擎 | Python 3.11+ |
| 桌面端 | TypeScript + Electron + React |
| 契约定义 | JSON Schema → Pydantic（Python）+ TypeScript 类型（生成） |
| Agent 编排 | LangGraph（仅执行平面） |
| 类型门禁 | mypy strict + ruff + Pydantic v2 |
| 模型适配 | 阿里云百炼（Qwen）默认，Anthropic 可切换 |
| 状态存储 | Git 仓库（文件真源）· SQLite（派生索引） |
| 任务隔离 | Git Worktree + 路径锁 |

---

## 安装

### Windows 安装包（推荐，无需 Node / Python）

桌面端提供自包含的 Windows 安装包，真实引擎与 sidecar 网关已用 PyInstaller 打包进安装目录（`resources/python/`），安装后无需 Node 或 Python 环境。

```bash
cd packages/desktop
npm run dist:win   # 引擎/sidecar 打包 + typecheck + 测试 + electron-builder NSIS
```

产物位于 `packages/desktop/release/`：`Codentum-Setup.exe`（NSIS 安装程序，约 130 MB）与 `win-unpacked/`（免安装目录版）。

**使用前提**：安装后需自行配置模型 API Key（`DASHSCOPE_API_KEY` 等）。安装包不携带任何凭据——未配置 Key 时引擎握手返回 `connected=false`、能力全部关闭，桌面端相应按钮禁用。

**发布门槛**：未经 Authenticode 签名的构建，Windows SmartScreen 会提示「未知发布者」；只有通过 `cold_start/verify-installer.ps1`（含真实引擎握手）并完成签名的构建，才可作为公开正式 Release。完整流程见 [`packages/delivery/docs/PACKAGING_RELEASE_RUNBOOK.md`](packages/delivery/docs/PACKAGING_RELEASE_RUNBOOK.md)。

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
| 建议配置 `budget_tracker` | 未配置则不写 `budget.json`，状态目录不完整，桌面端判定不连贯 |
| 门禁组件默认关闭 | `gate_runner` / `guardian` / `transition_table` 默认 `None`，走兜底验收；生产环境须显式配置 |

完整示例见 [`tests/e2e/test_abc_integration.py`](tests/e2e/test_abc_integration.py) 的 `TestRealExecution` 类，演示 pending → accepted 完整执行链路。

---

## 验证

```bash
make verify           # 全量验证
make verify-offline   # 零运行时依赖子集
```

| 命令 | 验证内容 | 可无依赖运行 |
|---|---|
| `make gen-check` | 生成物与 schema 一致性 | ✅ |
| `python scripts/validate_fixtures.py` | 固件过 schema + 八项交叉检查 | ✅ |
| `python scripts/check_boundaries.py` | 路径独占 · packages 全覆盖 · 契约冻结 | ✅ |
| `pytest tests/contract` | 契约用例（schema 自动生成） | ✅ |
| `python scripts/secret_scan.py` | 凭证扫描（工作区 + git 历史） | ✅ |
| `make typecheck` | mypy strict 五个 Python 包 | 需 `pip install -e ".[dev]"` |
| `make desktop-typecheck` | 桌面端 `tsc --noEmit` | 需 `npm i` |

前五项设计为零运行时依赖，在完整安装前即可验证契约自洽性。

### 判据自身的质量（按需运行，不进 CI 主链路）

| 命令 | 产出 | 耗时 |
|---|---|
| `python scripts/mutate_judgements.py --mode=strong` | 强变异存活率（判据**有没有人碰过**） | ~2 分钟 |
| `python scripts/mutate_judgements.py --mode=weak` | 弱变异存活率（判据**边界有没有测准**） | ~5 分钟起 |
| `python scripts/judgement_ledger.py` | 判据资产负债表（档位 × 命中 × 变异） | 秒级 |
| `python scripts/judgement_gaps.py` | 判据缺口报告（反复失败但事前无人拦） | 秒级 |

变异结果写入 `.codentum/judgements/mutation.json`，资产负债表读取它。这几项不放进 `make verify`：变异检验需运行数十遍测试套件，纳入主链路会让每次提交都付出该代价。它们是定期体检，不是回归门禁。

### 固件八项交叉检查

JSON Schema 无法表达跨文件关系约束，以下检查确保核心不变量在固件层面成立：

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

以下文件由代码生成，不应手写修改：

```text
packages/contracts/python/codentum_contracts/state.py   ← gen_types (Python)
packages/contracts/typescript/state.ts                 ← gen_types (TypeScript)
tests/contract/test_*.py                               ← gen_contract_tests
```

修改数据形状的标准流程：**修改 schema → `make gen` → 提交生成结果**。`make gen-check` 会拦截反向操作。手写两份相同逻辑会导致静默漂移——Python 端的解析与 TypeScript 端的渲染将对同一字段产生歧义。

---

## 许可

Apache-2.0（见 [`LICENSE`](LICENSE)）。推送前须通过 `secret-scan` 验证。

---

*Codentum = Code + Momentum。能力沉淀为质量，交付速度为速度，评测锚点确保已获得的能力不流失。*
