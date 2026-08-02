# 文档索引

设计文档就在代码仓库里，**跟着代码一起改，同一个 commit**。设计与实现脱节正是这套系统要消灭的问题之一，不能自己先犯。

---

## 四份主文档

| 文档 | 内容 | 什么时候读 |
|---|---|---|
| [00-总体设计方案.md](00-总体设计方案.md) | ★ 权威设计。五个平面、六条不变量、Harness/Loop/Graph 三横工程、四张图、上下文工程、自进化、模型路由、安全审计、测试体系 | 动手前通读一遍；之后按章节查 |
| [01-角色详细设计与Skill清单.md](01-角色详细设计与Skill清单.md) | 11 个角色逐个的 Harness / Loop / Graph 设计与巧思、Agent Identity 清单、Skill 清单与作用域 | 实现某个角色前 |
| [02-实施路线与交付计划.md](02-实施路线与交付计划.md) | P0–P7 阶段、MVP 边界、比赛里程碑、Demo 脚本、风险登记 | 排期、决定做不做某个东西 |
| [03-团队分工与协作规范.md](03-团队分工与协作规范.md) | 三人分工的完整方案与依据 | 入职一次，之后查 `../CONTRIBUTING.md` |

> 日常协作规则见根目录 [`CONTRIBUTING.md`](../CONTRIBUTING.md)——那是 03 的"每天要用的那几条"。

---

## [adr/](adr/) —— 架构决策记录

一个决策一个文件，只追加不改写。**动手前先看 [ADR-0002](adr/0002-语言与运行时选型.md)，它现在是红色待决状态，挡着所有实现代码。**

---

## 从设计到代码的对应关系

| 设计文档里的概念 | 代码位置 |
|---|---|
| 六条不变量（I1–I6） | `packages/contracts/` 定义，`packages/control-plane/admission/` 强制 |
| 状态设计 / 四张图 | `packages/contracts/state/` + `packages/control-plane/reconcile/` |
| 运行设计 / Loop | `packages/control-plane/reconcile/` + `state-machine/` |
| 执行设计 / Harness | `packages/harness/` |
| 上下文工程 / Context Broker | `packages/harness/context-broker/` |
| 角色体系 / Skill 体系 | `packages/roles/` |
| Guardian（确定性拦截，无 LLM） | `packages/control-plane/guardian/` |
| 门禁 / 绿线 | `packages/control-plane/gates/` |
| Provisioning / 冷启动复现 / 打包交付 | `packages/delivery/` |
| 可视化控制台 | `packages/desktop/` |

**这张表是双向的**：读代码不懂为什么这么设计 → 回去查文档；改了设计 → 对应的代码目录要跟着动。

---

## 写文档的三条约定

1. **不写版本号。** 版本在 git 里。
2. **不新建文档，改现有的。** 冗余信息比信息缺失更贵——四份主文档已经是收敛后的结果，别再发散。
3. **改设计要说清"否决了什么"。** 只写结论的文档，半年后没人知道能不能推翻它。

---

## 其他材料（不在本仓库）

| 路径 | 内容 |
|---|---|
| `D:\Run2AGI\项目进展与记忆.md` | 项目总索引与进展 |
| `D:\Run2AGI\Method\方案横评与选型决策.md` | 六套并行方案的十二维度横评与选型 |
| `D:\Run2AGI\Method\Codentum\Codentum-PPT\` | 参赛演示材料 |
| `D:\Run2AGI\赛题资料\` | 赛题原始文件 |
