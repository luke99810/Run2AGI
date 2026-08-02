# 协作规范

> 完整背景见 [`docs/03-团队分工与协作规范.md`](docs/03-团队分工与协作规范.md)。
> 这份是**每天要用的那几条**。

---

## 一、每人一个 worktree

三个人的 Agent 同时在一个工作区里跑，**一定会互相覆盖**。物理隔离 + 路径独占双层，跟系统内部的机制一样。

```bash
git worktree add ../codentum-a feat/control-plane   # A
git worktree add ../codentum-b feat/harness         # B
git worktree add ../codentum-c feat/desktop         # C
```

**Agent 只在自己的 worktree 里跑。**

---

## 二、契约只有 A 能改

改 `packages/contracts/**` 的流程：

```
提 ContractChangeRequest
  说清三件事：需要什么 / 为什么现有契约不够 / 建议的最小改动
      ↓
  A 评估
      拒绝 → 说明如何在现有契约下实现（多数情况是这个）
      接受 → ★ 变更窗口：通知受影响的人 → 改 → 记 ADR → 解冻
```

> **别人自己改契约，是这个项目最容易出现也最难查的问题。**
> 它会让另外两人的代码在毫无征兆的情况下失配。

同理，**这两处是生成物，任何人都不手写**（连 A 也不行）：

```
packages/contracts/python/codentum_contracts/state.py  ← gen_types（Python 侧）
packages/contracts/typescript/state.ts                ← gen_types（TS 侧，给桌面端）
tests/contract/test_*.py                              ← gen_contract_tests
```

改数据形状的正确流程：**改 schema → `python scripts/gen_types.py` → 提交生成结果**。
`make gen-check` 会拦住反向操作，并告诉你别改错了地方。

---

## 三、每日一次集成（绿线不变量的人类版本）

每天固定时间（建议下班前）：

```
1. rebase 到最新 main
2. 跑 契约测试 + 单元测试
3. 合入 → 打 tag
4. ★ 合入后冒烟失败 → 立即 revert，不现场修
```

**合入顺序按冲突风险升序**（改动小的先合）——跟系统里 Integrator 的规则一样。

**为什么不现场修**：现场修意味着绿线在修复期间一直破损，而这期间其他人的合入全部被阻塞。**绿线的价值在于它永远绿，为此值得浪费一次合入。**

---

## 四、评审关系：没有人评审自己的代码

```
A 的控制平面  →  B 评审        （B 是主要消费者，接口不好用他最先知道）
B 的 Harness  →  A 评审        （A 是不变量守护者）
C 的前端      →  B 评审
C 的交付链路  →  A 评审        ★ 且验收测试由 A 先写（安全关键）
```

**这条不能因为"赶时间"破例。** 它是整个质量体系的地基——跟系统里 Coder ↔ Reviewer 上下文隔离是同一条原则：**评判标准的制定者不能是被评判的人。**

---

## 五、周一定范围，周五砍范围

```
周一：本周做什么  +  ★ 本周明确不做什么（≥3 条）
周五：没做完的明确决定 —— 下周继续 / 砍掉 / 缩小
```

⚠️ **`wont_have` 每周必须写 ≥3 条。写不出来说明这周的边界没想清楚。**

> 三人 + Agent 移除了"一个人做不完"这堵反馈墙，**范围膨胀的代价不再自动显现**。
> 这条规则是替代那堵墙的。

---

## 六、提交前必做

```
□ make verify              ← 一条命令跑完六项检查
□ 改了 contracts/ ？        → 你不该改。提 ContractChangeRequest
□ 改了生成物 ？             → 你不该改。改 schema 再 make gen
□ 新增目录 ？               → 先在 boundaries.yaml 登记 owner，否则 check:boundaries 会红
□ 破坏了六条不变量中的任何一条 ？ → 停下，这是设计问题不是实现问题
```

`make verify` 已包含 `secret-scan`（工作区 + git 历史）。
**★ 这道门禁没有跳过开关**——一旦可豁免，它在最需要它的那天一定会被豁免。

---

## 七、提交信息

```
<type>(<scope>): <一句话>

type:   feat | fix | refactor | test | docs | chore | contract
scope:  contracts | control-plane | harness | roles | delivery | desktop | docker | ci
```

`contract` 类型的提交**必须附 ADR 链接**。

示例：

```
feat(control-plane): 路径锁的前缀树实现 + 乐观锁提交
contract(contracts): WorkPacket 增加 cost_usd 字段，见 ADR-0005
fix(desktop): 看板在空 graph.json 上崩溃
```

---

## 八、和 Agent 一起工作的三条

1. **Agent 生成的代码必须过对方评审** —— 不能自评，跟人写的一样
2. **Agent 只在自己的 worktree 里跑** —— 见第一条
3. **瓶颈不是打字速度，是验证** —— 与其让 Agent 多写，不如先把"什么算做完了"写清楚

> 每个任务开工前先写下**机器可判定的完成定义**。
> "做得好"不是完成定义，"在 3 个 golden-state 快照上渲染结果与预期截图一致"才是。
