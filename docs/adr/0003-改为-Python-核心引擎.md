# ADR-0003：核心引擎改用 Python，桌面端保持 TypeScript

- **日期**：2026-08-03
- **状态**：✅ 已接受
- **取代**：[ADR-0002](0002-语言与运行时选型.md)（全 TypeScript）
- **决策人**：A（队长）

---

## 为什么推翻 ADR-0002

ADR-0002 用四条理由选了全 TypeScript。其中三条现在依然成立，**但它漏掉了一个变量：团队的实际熟练度。**

事实：**三人都是 Python 熟、TypeScript 生疏。** 距初赛截止 15 天。

> **「团队写不动」是比「契约可能漂移」更硬的约束。**
> 一个理论上更优、但团队要边学边写的技术栈，在 15 天窗口里是净负债。

ADR-0002 的分析本身没错，错在**把一个没验证过的假设当成了已知**——它默认团队对两种语言的成本相同。这条假设从没被检查。

---

## 决策

```
packages/desktop/        TypeScript + React + Electron     （C）
packages/contracts/      JSON Schema（真源）→ 生成 Python + TS 两套类型
packages/control-plane/  Python 3.11+                      （A）
packages/harness/        Python 3.11+                      （B）
packages/roles/          Python 3.11+                      （B）
packages/delivery/       Python 3.11+                      （C）
scripts/                 Python                            （A）
```

**Electron 已定，所以桌面端必然是 TS——这一条没有变。** 真正改变的是核心引擎。

---

## 采纳的理由（用户提出，经核实成立的部分）

1. **RAG / 嵌入生态 Python 更厚。** sentence-transformers、faiss、llama-index 在 TS 侧只有薄替代。
2. **动态性让「Agent 写 Skill」更顺。** 生成即执行，无编译步骤。
3. **Aider、OpenHands 是 Python。**（Devin 闭源语言未公开、OpenClaw 实现不详，这两个不作为证据。）
4. **★ 团队熟练度** —— 决定性的一条。

---

## 没有采纳的理由（记下来，免得日后重复讨论）

| 说法 | 为什么不成立 |
|---|---|
| **「Node 动态性不如 Python」** | 不成立。`new Function()` / `vm.runInNewContext()` / 动态 `import()` 一样便宜。差别在**要不要让生成代码先过类型检查**，而那是选择不是限制 |
| **「RAG 密集场景」** | 本设计的检索确定性梯度是 `exact > structural > lexical > semantic`，语义检索是**兜底不是首选**；且生产 RAG 多数调嵌入 API（HTTP，语言无关） |

### ⚠️ 一处更正

本 ADR 初稿曾把「Python 适合复杂多 Agent 编排」也列进本表，理由是"本设计不用框架编排"。

**那句话说过头了。** 它只对**控制平面**成立，被错误地写成了覆盖全系统。
执行平面与各角色内部的 Loop 工程，LangGraph 是合适的——见 [ADR-0004](0004-编排框架-LangGraph-的适用层.md)。

这一条反而**加强**了本 ADR 的结论：LangGraph 以 Python 为主语言。

---

## 付出的代价（明确写下来，不粉饰）

### ① ★ 失去 `tsc` 这个免费的确定性验收谓词

这是 ADR-0002 最强的一条理由，现在没了。

**替代方案，且必须是不可绕过的：**

```
mypy --strict         类型检查
ruff                  lint
pydantic v2           ★ 运行时校验 —— TS 没有的东西，部分补偿了静态检查的损失
```

⚠️ **mypy 可以用 `# type: ignore` 绕过，`tsc` 不能。**

所以定一条硬规则：

> **`# type: ignore` 必须带具体错误码和一行理由注释，且进 CI 白名单计数。**
> 数量只许降不许升。裸的 `# type: ignore` 视同破坏门禁。

这条不写死，「能绕过的约束等于没有约束」就会应验在我们自己身上。

### ② 跨语言边界 = 契约漂移的高发区

**缓解措施（已落地）：契约的真源本来就是 JSON Schema，不是 TS。**

```
packages/contracts/schemas/*.json     ← 唯一真源
        ├── gen_types.py --lang py  → Python (Pydantic 模型)
        └── gen_types.py --lang ts  → TypeScript (供 desktop)
```

`gen:check` 同时校验两套生成物。**两侧都是生成物，谁都不手写 → 漂移在机制上被堵住。**

> 这是当初把真源放进 JSON Schema 而不是 TS 类型的意外回报：换语言的代价从"重写契约"降成了"加一个发射器"。

### ③ 打包复杂度上升，且落在经验最薄的成员身上

Electron + Python sidecar 明显比纯 Node 难。

**方案**：Python 引擎作为**本地服务**（stdio 或 localhost HTTP）由 Electron 拉起；发布时用 PyInstaller 打成单可执行文件随包分发。

⚠️ **这是本次变更最大的执行风险。** 建议 C 在 P1 阶段就打通一次最小链路（Electron 拉起 Python、通一次消息、打包跑通），**不要等到最后一周**。冷启动复现测试必须覆盖它。

### ④ 已完成工作的返工

```
保留（语言无关）  8 份 schema(641 行) · 24 个固件 · 4893 行设计文档 · boundaries.yaml
重写              473 行手写接口 → Python Protocol
重生成            408 行类型 → Pydantic 模型 + TS 类型
移植              1639 行 Node 工具脚本 → Python（★ 团队要能维护它们）
```

约一天。**这也是现在决定而不是两周后决定的原因。**

---

## 保持不变的部分

以下决定与语言无关，全部沿用：

- 六条不变量（I1–I6）
- 控制平面零 LLM
- 状态存 Git 仓库，不是数据库
- 契约冻结（I3）与 `boundaries.yaml` 路径独占
- 预算用货币不用 token
- 同一次尝试内不换模型，要加强就提高 effort
- 模型隔离：coder ≠ reviewer，evolver ≠ verifier
- 8 份 schema 的内容（一个字段都没改）

---

## 后果

**正面**

- 三人用熟悉的语言，15 天窗口内不必边学边写
- Agent / RAG 生态可用（真需要时）
- Pydantic 的运行时校验是 TS 侧没有的能力

**代价**

- 见上方四条，尤其是①和③

**风险**

- ⚠️ Electron + Python 打包若在最后一周才做，会成为交付事故。**P1 阶段必须打通。**
- ⚠️ `# type: ignore` 若失控，第①条的替代方案就形同虚设。**CI 计数，只降不升。**

---

## 什么会让我们再改回去

诚实起见记一下——**如果以下任一成立，应重新评估**：

```
□ Python 端的类型检查在实践中被大量 ignore 绕过（说明替代方案失败）
□ Electron + Python 打包成为持续的时间黑洞
□ 跨语言契约在 gen:check 之外仍出现漂移（说明生成器覆盖不全）
```
