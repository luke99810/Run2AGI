# roles —— 角色体系

11 个角色的定义、提示词、Skill。

---

## 一个角色是什么

> **一个角色 = 一组写权限 + 一组可见上下文 + 一组可触发的状态转换。**
> **提示词只是让它高效地用好这组权限。**

这个定义决定了本 package 的结构：`specs/` 是角色的真身，`prompts/` 只是让它跑得顺，`skills/` 是可插拔的能力。

**不要反过来**——不要靠写提示词去实现约束。约束在 `specs/` 里声明，由 harness 物理生效。

---

## 归属

| | |
|---|---|
| owner | **B** |
| 评审 | A |
| 依赖 | `contracts` |

---

## 目录

| 目录 | 内容 |
|---|---|
| `specs/` | ★ RoleSpec —— 每个角色一份。**Single Source**，派生四处配置 |
| `prompts/` | 角色提示词。可以迭代，但不承载硬约束 |
| `skills/` | Skill 定义。五态状态机 + 三级作用域 |

### 内置 Skills

| Skill | 绑定角色 | 用途 |
|---|---|---|
| `requirements` | intake | 需求澄清、假设记录、可验收 brief |
| `architecture` | architect | 契约、ADR、模块边界与权限面 |
| `planning` | planner · manager | WorkPacket 拆分、依赖图、调度决策 |
| `frontend` | coder · helper | React / TypeScript / 桌面端 UI 实现与验证 |
| `backend` | coder · helper | engine / harness / delivery / control-plane 后端实现 |
| `testing` | qa · coder · integrator · helper | 验收测试、回归验证、绿线证据 |
| `debugging` | coder · helper · integrator | 失败测试、阻塞 Worker、运行时接缝诊断 |
| `review` | reviewer · integrator · evolver · guardian | 对抗评审、证据审计、边界检查 |
| `security` | architect · reviewer · guardian | 权限、密钥、命令执行、网络与注入风险审计 |
| `integration` | integrator | 已评审变更整合、绿线验证、集成证据 |
| `cost-governance` | planner · manager | token / 模型成本、预算降级、CNY 归因 |
| `evolution` | evolver | 从失败与评审证据中沉淀候选 Skill / Policy |

每个 Skill 都有 `manifest.json` 与 `SKILL.md`。RoleSpec 只引用 Skill id、scope、state；
loader 会校验引用的 Skill manifest 必须存在、id 与目录名一致，且 `SKILL.md` 可读。
引擎启动时会把被 RoleSpec 引用的内置 Skill 投影到项目共享空间
`.codentum/skills/shared/<id>/`，Local/Team Worker 写 PromptBundle 时优先从这个共享目录
读取 `SKILL.md`；没有共享目录时才回退到内置源。`PromptBundle` 只注入 state 为空或
`active` 的 Skill 正文。这样 C 的 Skills 面板从项目 RoleSpec 派生选项时，背后有 B 的
真源文件、项目共享副本和运行时输入链路，而不是静态字符串。

### RoleSpec 派生四处

```
                  RoleSpec
        ┌────────────┬───────────┬────────────┐
        ↓            ↓           ↓            ↓
     工具面        卷挂载    状态转换表    所有权注册
   (harness)     (harness) (control-plane) (control-plane)
```

**四处都不许手工维护。** 手工维护的漂移特征是：权限悄悄变宽，而且不报错。

---

## 11 个角色

| 角色 | 一句话 | 碰模型 |
|---|---|---|
| Intake | 把人话变成可判定的需求 | ✓ |
| Architect | 定契约与模块边界 | ✓ |
| Planner | 切 WorkPacket，排依赖 | ✓ |
| QA | ★ **在实现之前**写验收测试 | ✓ |
| Coder ×N | 实现 | ✓ |
| Helper | 分级求助 L1：被卡住时介入 | ✓ |
| Reviewer | 评审 | ✓ |
| Integrator | 集成与绿线 | ✓ |
| Manager | 调度决策的模型侧 | ✓ |
| Evolver | 经验晋升与规则沉淀 | ✓ |
| **Guardian** | 确定性拦截 | ✗ **无 LLM** |

详细设计见 [`../../docs/01-角色详细设计与Skill清单.md`](../../docs/01-角色详细设计与Skill清单.md)。

---

## 三层制衡（这是本 package 存在的意义）

| 维度 | 机制 |
|---|---|
| **权限隔离** | 各角色 `owns_paths` 不相交 |
| **上下文隔离** | ★ Reviewer 读不到 Coder 的推理链（存储层强制，不是靠提示词） |
| **模型隔离** | ★ `Coder ≠ Reviewer` 的模型；`Evolver ≠ Verifier` 的模型 |

**模型隔离是为了错误去相关。** 同一个模型既写又审，它的盲区会在两处同时出现——评审就成了摆设。这两条是硬约束，写在 RoleSpec 里由代码校验，不是建议。

### 三种作弊必须被结构性堵死

| 作弊 | 堵法 |
|---|---|
| 改测试让它过 | 测试目录只读挂载 |
| 改契约让它符合实现 | `contracts` 只读挂载 |
| 缩小范围假装做完 | 验收谓词由 QA 先写，Coder 看不见也改不了 |

**三条都靠挂载权限，不靠提示词。**

---

## 完成定义

```
□ 11 份 RoleSpec 齐全，且都能通过 rolespec.schema.json 校验
□ ★ 四处派生全部由代码生成，无手工维护点（用生成脚本证明）
□ ★ Coder 与 Reviewer 的模型不同，由校验代码强制，违反则拒绝启动
□ ★ Reviewer 的上下文里取不到 Coder 的推理链（写测试证明取不到）
□ 三种作弊各有一个红队用例，且都失败
□ Skill 的五态状态机转换完整，非法转换被拒
```

---

## 硬约束

1. **提示词里不承载硬约束。** 硬约束进 RoleSpec，由 harness 物理生效。判据：**这条约束如果 Agent 不配合会怎样？** 会失效 → 那它不该在提示词里。
2. **Guardian 里不许有模型调用。**
3. **新增角色必须先加 RoleSpec，再加提示词。** 反过来做，会出现"提示词里有但权限上没有"的能力幻觉。
4. **Skill 的作用域要显式声明**（全局 / 角色 / 单次），默认最小。
