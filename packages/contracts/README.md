# contracts —— 契约层

> ★ **只有 A 能写。** 其他人需要改 → 提 ContractChangeRequest，见根目录 `CONTRIBUTING.md` 第二条。

---

## 职责

**做什么**：定义所有跨模块的东西——数据形状（schema）、接口签名（interfaces）、状态类型（state）。

**明确不做什么**：
- ✗ 不含任何实现逻辑（这里只有类型和 schema，没有函数体）
- ✗ 不含只在单个 package 内部用的类型（那些留在各自 package 里）
- ✗ 不含提示词、模型配置、UI 文案

**为什么单独成包**：契约是三人协作的接缝。它不冻结，另外两人的工作就都建在流沙上。

---

## 归属

| | |
|---|---|
| owner | **A** |
| 评审 | B（主要消费者。接口不好用，B 最先知道） |
| 变更流程 | ContractChangeRequest → A 评估 → 变更窗口 → 记 ADR → 解冻 |

---

## 依赖

**依赖谁**：无。这是依赖图的根。

**被谁依赖**：全部五个 package。所以这里改一行，可能牵动所有人——这正是它要冻结的原因。

---

## 目录

| 目录 | 内容 | 手写？ |
|---|---|---|
| `schemas/` | ★ **真源**。8 份 JSON Schema：identifiers（共享标识）/ workpacket / rolespec / graph / knowledge / budget / evidence / decision | ✓ 手写 |
| `python/codentum_contracts/interfaces.py` | 四个核心接口 + 进程内传参类型。★ 只有 Python 一侧 —— 桌面端是纯读端，不实现它们 | ✓ 手写 |
| `python/codentum_contracts/state.py` | 落盘数据的 Pydantic 模型 | ❌ **生成物** |
| `typescript/state.ts` | 同上，供桌面端 | ❌ **生成物** |

**判据：它会不会出现在磁盘上的某个 JSON 里？**
会 → 写 schema，两侧类型自动生成。不会（如 `MountSpec`、`SpawnRequest`）→ 手写在 `interfaces.py`。

### 四个接口为什么是这四个

它们是**平面之间唯一的通道**：

| 接口 | 分隔的两侧 |
|---|---|
| `WorkerRuntime` | 控制平面 ↔ 执行平面 |
| `ArtifactStore` | 执行平面 ↔ 数据平面 |
| `MemoryIndex` | 上下文平面 ↔ 数据平面 |
| `ModelGateway` | 执行平面 ↔ 模型供应商 |

定死这四个签名，等于定死了平面之间怎么说话。**其余一切都是各自平面的内部实现，可以随便重写。**

---

## 完成定义（第 0 周）—— ✅ 全部达成

```
✅ 四个接口签名定死，通过类型检查
✅ 8 份 JSON Schema 完整且能校验；identifiers 为共享真源，其余 $ref，无重复 enum
✅ state 类型 Python + TS 两套均由 gen_types 生成，幂等，gen-check 可拦手改
✅ tests/contract/ 由 gen-contract-tests 生成，105 个用例全绿
✅ 3 个 golden-state 快照通过 schema + 八项交叉检查
✅ 8 份 schema 逐字段过审完毕
✅ boundaries.yaml frozen_at = 2026-08-02
```

### 过审时改掉的四处

| 问题 | 修法 |
|---|---|
| `PacketState` 在 workpacket 和 rolespec 各写一份 enum | 移进 `identifiers`，两处 `$ref` |
| 审计链链首哨兵是裸 64 个 0，其余摘要带 `sha256:` 前缀 | 统一为 `sha256:` + 64 位，加 pattern。**哨兵也必须是合法格式，否则解析链首要走特例分支，而特例分支是最少被测到的分支** |
| `reasonCode` / `action` 是自由字符串 | 加 pattern 禁止空格与中文——这是「机器可读的理由码，不是自然语言」的**可执行版本** |
| `mustDifferFrom` / `escalateTo` / `transitions.from,to` 是裸 string | 改 `$ref` 到 RoleId / PacketState，打错字编译期就红 |

### 一条 schema 保证不了的约束（别误以为它被检查过）

`RoleSpec.usesModel`：**id = guardian 时必须为 false**。JSON Schema 表达不了这种条件约束，由加载 RoleSpec 的代码强制。schema 描述里已写明这一点。

> 最后一条是标志：**契约冻结日 = 第 0 周结束日 = 其他两人正式开工日。**

---

## 硬约束

1. **`ModelGateway` 的预算字段用货币，不用 token。**
   不同模型家族的分词器差异可达 ~30%。异构路由下按 token 记预算会静默失真，且失真方向随路由变化——查都没法查。

2. **schema 是 Single Source，`state/` 从 schema 生成，不手写。**
   手写两份必然漂移，且漂移时不报错。`make gen-check` 是这条的执行者。
   新增 schema 要到 `scripts/gen_types.py` 的 `PLAN` 里登记——**漏登记会报错，不会静默兜底**。
   ★ 跨语言后这条更要紧：两侧都是生成物，是 ADR-0003 唯一有效的防漂移手段。

3. **接口签名一旦冻结，只允许加实现不允许改签名。**
   要改需三人同意 + 新 ADR + 走变更窗口。

4. **不要在这里塞"顺手也放这儿吧"的东西。**
   契约层每多一个类型，冻结的成本就高一分。判据：**它是否跨越 package 边界？** 不跨越就不该在这。

---

## 现状：★ 已冻结（2026-08-02）

8 份 schema 与 4 个接口签名均已过审冻结。此后**只允许加实现，不允许改签名**。

> ⚠️ 若 B / C 还没实际过目，把 `boundaries.yaml` 的 `frozen_at` 改回 `null`，走完确认再填。
> **冻结的意义在于三个人都认它。**
