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
├── tests/                  contract（自动生成）· e2e
├── docker/            C    冷启动容器 · Team 模式 · 交付运行时
├── scripts/           A    开发与验证脚本
└── boundaries.yaml         ★ 团队路径独占表（与系统内部机制同构）
```

**路径独占：没有任何一格是两个 owner。** 详见 [`boundaries.yaml`](boundaries.yaml)。

---

## 第 0 周：契约冻结 ✅ 已完成（2026-08-02）

> **这一周产出的是契约，不是代码。**
> 跳过它直接分头写，三周后集成会撞上合并地狱——而这套系统存在的理由就是消灭那个地狱。

```
✅ 目录骨架
✅ 语言与运行时选型       ★ Python 核心引擎 + TS 桌面端，ADR-0003（取代 0002）
✅ 编排框架              LangGraph 用在执行平面，不用在控制平面，ADR-0004
✅ 工程底座              pyproject（mypy strict + ruff）· 5 个 Python 包 + 1 个 TS 包
✅ 8 份 JSON Schema      identifiers 为共享真源，其余一律 $ref，无重复 enum
✅ 四个接口签名           packages/contracts/python/codentum_contracts/interfaces.py
✅ 状态类型（★ 生成物）    ★ Python(Pydantic) + TS 两套，同源于 schema
✅ 3 个 golden-state 快照  empty · mid-flight · blocked，各带 NOTE.md
✅ 五个工程脚本           全部 Python，零依赖：gen_types · gen_contract_tests ·
                        validate_fixtures · check_boundaries · secret_scan
✅ 105 个契约测试         正例来自真实固件，反例由固件变异而来
✅ 路径独占表冻结          boundaries.yaml: frozen_at = 2026-08-02
```

> ⚠️ **`frozen_at` 等于「宣布冻结」。** 若 B / C 还没过目全部 8 份 schema 与 4 个接口签名，
> 把它改回 `null`，走完确认再填——**冻结的意义在于三个人都认它。**

**此后只允许加实现，不允许改接口签名。** 要改需三人同意 + 新 ADR + 走变更窗口。

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
| `pytest tests/contract` | 105 个契约用例 | ✅ |
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

### 契约冻结后，各人的第一件事

| | 第一件事 | 完成定义（机器可判定） |
|---|---|---|
| **A** | `git init` + 首次提交；路径锁的前缀树 | 并发申请重叠路径，有且只有一个成功 |
| **B** | Harness 三段式骨架 + 工具面从 RoleSpec 派生 | 角色无权限的工具**不出现在工具列表里**（不是被拒，是看不见） |
| **C** | 拿三个 golden-state 快照渲染看板 | 三个快照渲染结果与预期截图一致，且 **empty 快照不崩** |

> ⚠️ **`git init` 是现在唯一必须人工做的一步。**
> 在此之前：`check-boundaries` 的 I3 契约冻结检查、`secret-scan` 的历史扫描
> 这两项**都处于「无法执行」状态**——不是通过，是没跑。

---

## 文档

| 文档 | 内容 |
|---|---|
| [`docs/00-总体设计方案.md`](docs/00-总体设计方案.md) | **权威依据**。26 章完整设计 |
| [`docs/01-角色详细设计与Skill清单.md`](docs/01-角色详细设计与Skill清单.md) | 每个角色的 Harness/Loop/Graph 精密设计 + Skill 清单 |
| [`docs/02-实施路线与交付计划.md`](docs/02-实施路线与交付计划.md) | P0–P7 分期、MVP 边界、Demo 剧本 |
| [`docs/03-团队分工与协作规范.md`](docs/03-团队分工与协作规范.md) | 三人分工、协作规范、14 天冲刺 |
| [`docs/adr/`](docs/adr/) | 架构决策记录（含已否决的方案与理由） |

---

## 开发环境

**日常开发不需要 Docker。** Solo 模式的设计就是本地单进程 + 本地 Git + SQLite，零外部依赖。

```bash
pip install -e ".[dev]"    # Python 核心引擎
cd packages/desktop && npm i   # 桌面端
```

⚠️ **Electron 拉起 Python 引擎 + PyInstaller 打包是 ADR-0003 引入的最大执行风险。**
P1 阶段就要打通一次最小链路，不要等到最后一周。

Docker 只在三个场景出现：

| 用途 | 谁需要 |
|---|---|
| 冷启动复现测试的干净容器 | C |
| Team 模式验证（AgentTeams 全栈） | A 或 B，各一次 |
| 给评委的一键复现 | C |

详见桌面上的《Codentum 基础设施与部署路线》。

---

## 许可与合规

- **License**：Apache-2.0（见 [`LICENSE`](LICENSE)）
- **依赖披露**：见 `docs/` 中的依赖披露表（⚠️ 含 MinIO 的 AGPL 说明）
- **凭证**：本仓库 `.gitignore` 已排除全部凭证文件；**推送前必过 `secret-scan`——自己的产品自己先用**

---

*Codentum = Code + Momentum。动量 = 质量 × 速度，且无外力时守恒——
能力沉淀是质量，交付速度是速度，而 L4 评测锚点保证已获得的能力不会流失。*
