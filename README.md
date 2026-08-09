# Codentum

> **Code + Momentum** —— 自进化软件开发多 Agent 系统
>
> 一款本地优先的桌面软件：输入一个软件需求，它编排一支 Agent 团队完成开发，
> 全过程在可视化界面中可见，最终一次性收集凭证与素材，做生产级测试并打包交付。

---

## 这个系统在赌什么

> **多 Agent 系统的可靠性来自不变量，不来自提示词。**
>
> 凡是"在提示词里叮嘱 Agent 不要做 X"的地方，都应改成"在结构上让 X 做不到"。

推论——**把不可恢复的部分交给确定性代码，把可恢复的部分交给模型**：控制平面（状态机、锁、准入校验、门禁）出错不可恢复，所以一行 LLM 都不用；执行平面（写代码、评审）出错可恢复（打回重试），所以交给模型。

完整设计见 [`docs/00-总体设计方案.md`](docs/00-总体设计方案.md)。

---

## 六条不变量（骨架，不可破）

角色、流程、工具都可以换，这六条不能。任何 PR 破坏其中一条都应被拒绝。

| ID | 名称 | 内容 | 强制方式 |
|---|---|---|---|
| **I1** | 单写者 | 任一路径同一时刻只被一个 in_progress packet 拥有 | 所有权图 + 准入校验 + git hook |
| **I2** | 验收可判定 | 每个 packet ≥1 条机器可判定的验收谓词 | 准入校验器（确定性） |
| **I3** | 契约冻结 | 只有 Architect 能写 `contracts/`，Coder 只读 | 只读挂载 + hook + Guardian |
| **I4** | 绿线 | main 任何时刻可构建、可部署、E2E 通过 | 合入前在最新 main 重跑验收 |
| **I5** | 单会话闭合 | 一个 packet 必须在一个会话预算内完成 | 预算计数器 + 自动 Splitter |
| **I6** | 证据 | 状态推进必须附证据引用，声明不算 | 状态机转换校验 |

**约束的实现优先级：不可见 > 无权限 > 被拦截 > 提示词劝阻。**

> I6 的完整口径是「**执行完成但证据没落盘 = 没做过**」。
> 注意区分**证据**和**簿记**：控制平面自己写的流水（拿到了锁、worker 失败了）
> 带 `sys:` 前缀，验收时一律排除 —— 拿它当验收依据，等于系统自己给自己签字。

---

## 它是怎么跑起来的

### 两个平面

```
┌─────────────────────────────────────────────────┐
│  控制平面  control-plane        零 LLM · 确定性  │
│  状态机 · 路径锁 · 准入校验 · 门禁 · 预算 · Guardian │
└───────────────┬─────────────────────────────────┘
                │ WorkerRuntime（唯一入口）
┌───────────────▼─────────────────────────────────┐
│  执行平面  harness              模型在这里跑      │
│  git worktree 隔离 · 上下文装配 · prompt · 证据    │
└─────────────────────────────────────────────────┘
```

**边界判据**：把执行平面整个换掉（换框架、换模型、换 Agent 实现），
控制平面应当**一行都不用改**。

### WorkPacket 的一生

一个 WorkPacket 是一份"谁、在哪些路径上、做什么、怎么算做完"的声明。
调和循环（`reconcile`，对标 K8s controller manager）反复地看每个 packet
当前是什么状态、能不能推进、然后落盘：

```
pending ──► ready ──► running ──► review ──► accepted
   │                     │           │
   │                     │           └──► rejected
   └──► blocked ◄────────┘                （可打回重来）
```

| 转换 | 判据 |
|---|---|
| `pending → ready` | 依赖的 packet 全部 accepted |
| `ready → running` | **拿到 `ownsPaths` 的全部路径锁**（I1 的强制点） |
| `running → review` | worker 结束（成功或失败都进 review） |
| `review → accepted` | 门禁通过；无门禁时兜底要求**至少一条真实证据**且 worker 未失败 |

调和是**幂等**的：同一状态跑 N 次 tick 结果一样，副作用只发生一次。
所以进程崩了重启、从 `.codentum/` 重新 `load_state()` 就能接着跑。

### 状态目录 `.codentum/`

**这是控制平面与桌面端之间唯一的接口** —— 一边写，一边读，没有 RPC：

```
.codentum/
├── graph.json          必需  依赖图 + 所有权图（锁表）。dependency 是 packets/ 的投影
├── packets/            必需  每个 WorkPacket 一个 JSON，磁盘是唯一真源
├── budget.json         必需  预算账本（花了多少、按角色/模型分摊）
├── decisions.jsonl     必需  决策流水（空文件合法）
├── evidence/           必需  证据产出物（空目录合法）
├── knowledge/          可选  知识条目
└── roles/              可选  RoleSpec 覆盖
```

前五个**缺一不可**：桌面端加载时逐项校验，缺任何一个都会把整份快照判为
不连贯（`coherent = false`）并在界面上提示，而不是默默显示半份数据。
空的 `decisions.jsonl` 与空的 `evidence/` 目录本身是合法状态 ——
"没有决策"和"没有这个文件"是两回事，前者是信息，后者是残缺。

两侧的读写各有一套校验（写侧 Pydantic，读侧手写守卫），
它们没有共同的生成源，所以 `tests/e2e` 里有一条**跨语言**用例盯着这条接缝：
让控制平面真的跑一轮落盘，再用桌面端自己的加载器读回来。

---

## 目录地图

```
codentum/
├── docs/                   设计文档（权威）+ ADR
├── packages/
│   ├── contracts/     A    ★ 契约冻结 —— schemas/(真源) → python/ + typescript/(生成)
│   ├── control-plane/ A    Python。确定性内核：状态机 · 锁 · 准入 · 门禁 · 预算 · Guardian
│   ├── harness/       B    Python + LangGraph。三段式外壳，只有中段有模型
│   ├── roles/         B    Python。RoleSpec · prompt · Skill
│   ├── delivery/      C    Python。Provisioning · 凭证扫描 · 打包 · 冷启动测试
│   └── desktop/       C    ★ TypeScript。Electron 壳 · 视图 · 管理模块 · 交互界面
├── fixtures/          A    ★ golden-state 快照 —— C 的解耦点
├── tests/                  contract（自动生成）· e2e（跨包接缝）
├── docker/            C    冷启动容器 · Team 模式 · 交付运行时
├── scripts/           A    开发与验证脚本
└── boundaries.yaml         ★ 团队路径独占表（与系统内部机制同构）
```

**路径独占：没有任何一格是两个 owner。** 详见 [`boundaries.yaml`](boundaries.yaml)。

---

## 快速上手

```bash
pip install -e ".[dev]"          # Python 核心引擎
cd packages/desktop && npm i     # 桌面端
```

最小可运行链路 —— 让控制平面驱动一个真实 worker：

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
loop.load_state()                       # 磁盘是唯一真源
loop.worker_runtime = build_local_worker_runtime(
    LocalWorkerRuntimeConfig(
        repo_root=project,
        runner=RunnerConfig.command_runner([...], timeout_seconds=900),
    )
)
loop.run_until_stable(max_ticks=30)
loop.save_state()
```

### 装配须知（这几条不写在任何接口签名上）

| 要点 | 不满足会怎样 |
|---|---|
| **`runner` 必须显式配置** | 默认 `None` = 没有执行器。worker 会建好 worktree、写好 prompt bundle，然后以 `runtime_error: no worker runner configured` 收场 —— **任何 packet 都不可能被真实执行** |
| **`repo_root` 必须是被开发的项目仓库，且是 git 仓库** | worktree 隔离要跑 `git rev-parse`；worker 工作区是 repo_root 的**兄弟目录**（`<父目录>/codentum-workers/<pid>/attempt-N`），填错会一直失败 |
| **`budget_tracker` 建议配上** | 不配则不写 `budget.json`，状态目录不完整，桌面端判为不连贯（会打 warning，不会静默） |
| **门禁组件默认关闭** | `gate_runner` / `guardian` / `transition_table` 默认 `None`，此时走的是兜底验收。**生产用法应当显式配齐** |

可运行的完整例子见 [`tests/e2e/test_abc_integration.py`](tests/e2e/test_abc_integration.py)，
其中 `TestRealExecution` 演示了从 `pending` 到 `accepted` 的真实一轮。

---

## 验证：一条命令

```bash
make verify          # 全部
make verify-offline  # ★ 不需要 pip install -e ".[dev]" 就能跑的那部分
```

| 命令 | 验什么 | 装依赖前能跑？ |
|---|---|---|
| `make gen-check` | 生成物与 schema 一致（手改 → 红） | ✅ |
| `python scripts/validate_fixtures.py` | 固件过 schema + **八项交叉检查** | ✅ |
| `python scripts/check_boundaries.py` | 路径独占 · packages 全覆盖 · 契约冻结 | ✅ |
| `pytest tests/contract` | 契约用例（由 schema 生成） | ✅ |
| `python scripts/secret_scan.py` | 凭证扫描（工作区 + git 历史） | ✅ |
| `make typecheck` | mypy --strict 五个 Python 包 | 需 `pip install -e ".[dev]"` |
| `make desktop-typecheck` | 桌面端 `tsc --noEmit` | 需 `npm i` |

> **前五个刻意做成零运行时依赖**——它们要在 `pip install -e ".[dev]"` 之前就能跑，
> 否则第一天拉下仓库的人**没法确认自己拿到的是一份自洽的契约**。

### `validate_fixtures` 的八项交叉检查

JSON Schema 表达不了跨文件的关系约束，而这几条恰恰是全案的核心不变量：

```
I1 路径不相交        running packet 的 ownsPaths 两两不重叠
验收制衡            acceptance.authoredBy ≠ role
锁持有者为 running   ★ 卡住即释放锁，否则一个卡住的任务锁死整条流水线
依赖图无环           必须是 DAG
图与文件一致         graph.json 的节点 ↔ packets/ 的文件
I6 审计链首尾相接    断链即篡改，这条不能只写在文档里
证据引用有效         packet.evidence 指向的证据必须存在
无凭证              固件里不许有真密钥
```

### 写测试时的一条硬规矩

> **写完一条测试，先问：如果这个功能坏了，它会不会红？**

这条不是客套话，是踩出来的。本项目已经吃过两次亏：

- 并发用例第一版是**空转**的 —— 把互斥锁整个换成空操作，跑 400 轮五线程抢重叠
  路径，依然每轮恰好一个赢家（临界区太短，GIL 没机会切走）
- 全链路用例断言"状态到达 accepted"，而 worker 其实全部失败了 ——
  这类断言**在放行逻辑越松时越容易通过**，系统坏掉时反而绿得最稳

所以：**涉及不变量的测试，必须配一次「把约束摘掉看它会不会红」的对照实验。**
新写的测试第一次就绿，先怀疑它没跑。

---

## ★ 两处是生成物，任何人不手写（连 A 也不行）

```
packages/contracts/python/codentum_contracts/state.py  ← gen_types（Python 侧）
packages/contracts/typescript/state.ts                ← gen_types（TS 侧，给桌面端）
tests/contract/test_*.py                              ← gen_contract_tests
```

改数据形状：**改 schema → `make gen` → 提交生成结果**。`gen:check` 会拦住反向操作。

> **手写两份必然漂移，而漂移时不报错**——它只是悄悄不一致，
> 直到某天 B 写的解析和 C 写的渲染对同一个字段有了不同理解。

---

## 三人分工

| | A（队长） | B | C |
|---|---|---|---|
| 主责 | 控制平面 + 契约 | Harness + 角色实现 | 桌面端 + 交付链路 |
| 本质 | 定规则的人 | 让规则跑起来的人 | 把系统变成产品的人 |
| 爆炸半径 | **最大**（锁判错即数据损坏） | 中（执行错可打回重试） | **最小**（Console 零写权限） |
| 能否独立开工 | 是 | 依赖 contracts | **是**（有 golden-state） |

完整分工与协作规范见 [`docs/03-团队分工与协作规范.md`](docs/03-团队分工与协作规范.md)，日常规则见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。

**契约冻结后只允许加实现，不允许改接口签名。** 要改需三人同意 + 新 ADR + 走变更窗口
（`boundaries.yaml` 的 `frozen_at` 是这条规矩的锚点）。

---

## 文档

| 文档 | 内容 |
|---|---|
| [`docs/00-总体设计方案.md`](docs/00-总体设计方案.md) | **权威依据**。26 章完整设计 |
| [`docs/01-角色详细设计与Skill清单.md`](docs/01-角色详细设计与Skill清单.md) | 每个角色的 Harness/Loop/Graph 精密设计 + Skill 清单 |
| [`docs/02-实施路线与交付计划.md`](docs/02-实施路线与交付计划.md) | 分期路线、MVP 边界、Demo 剧本 |
| [`docs/03-团队分工与协作规范.md`](docs/03-团队分工与协作规范.md) | 三人分工与协作规范 |
| [`docs/04-模型路由表.md`](docs/04-模型路由表.md) | 按「错了有多贵」分档，不按「活有多难」 |
| [`docs/adr/`](docs/adr/) | 架构决策记录（含已否决的方案与理由） |

---

## 开发环境

**日常开发不需要 Docker。** Solo 模式的设计就是本地单进程 + 本地 Git + SQLite，零外部依赖。

Docker 只在三个场景出现：

| 用途 | 谁需要 |
|---|---|
| 冷启动复现测试的干净容器 | C |
| Team 模式验证（AgentTeams 全栈） | A 或 B，各一次 |
| 一键复现包 | C |

⚠️ **Electron 拉起 Python 引擎 + PyInstaller 打包是 ADR-0003 引入的最大执行风险**
（换 Python 换来了团队熟悉度，代价落在打包这一环），需要尽早打通一次最小链路。

---

## 许可与合规

- **License**：Apache-2.0（见 [`LICENSE`](LICENSE)）
- **依赖披露**：见 `docs/` 中的依赖披露表（⚠️ 含 MinIO 的 AGPL 说明）
- **凭证**：本仓库 `.gitignore` 已排除全部凭证文件；**推送前必过 `secret-scan`——自己的产品自己先用**

---

*Codentum = Code + Momentum。动量 = 质量 × 速度，且无外力时守恒——
能力沉淀是质量，交付速度是速度，而 L4 评测锚点保证已获得的能力不会流失。*
