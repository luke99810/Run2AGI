# golden-state —— 状态快照

`.codentum/` 在某一时刻的完整快照。**第 0 周必须产出 ≥3 个。**

---

## 一个快照长什么样

```
golden-state/
└── <名字>/
    ├── NOTE.md            ★ 必须有：这个快照代表什么情况、怎么产生的、关键字段是哪些
    └── .codentum/
        ├── graph.json         依赖图 + 所有权图
        ├── packets/           WorkPacket
        ├── evidence/          证据
        ├── decisions.jsonl    决策记录（追加）
        └── budget.json        预算
```

> 具体文件名以 `packages/contracts/state/` 定稿为准。第 0 周定契约时一并定死，之后不再改。

---

## 第 0 周的三个必备快照

| 名字 | 情况 | 主要验什么 |
|---|---|---|
| `empty/` | 刚 init，什么都没有 | ★ 空态不崩 |
| `mid-flight/` | 多 packet 并行中，有锁占用、有排队 | 主路径渲染 |
| `blocked/` | 有卡住的 packet、待审批、超预算 | 异常态呈现 |

`empty/` 看起来最没用，实际上最常暴露 bug——**"没有数据"是所有渲染代码的第一个边界，也是最常被跳过的那个。**

---

## NOTE.md 的模板

```markdown
# 快照：<名字>

**代表的情况**：一句话
**产生方式**：手工构造 / 从 <某次运行> 导出
**关键字段**：
- `budget.json:remaining_usd` = -3.2 —— 故意为负，用来测超预算告警
- `packets/p-007.json:state` = blocked —— 卡在依赖未满足
**已知不完整之处**：（有就写，没有写"无"）
```

没有 NOTE.md 的快照，**半年后没人知道那个负数是 bug 还是故意的**。

---

## 硬约束

1. **必须通过 schema 校验**，且这条校验进 CI。
2. **不许有真实凭证。** 用 `REPLACE_ME_xxx` 占位。
3. **改快照必须同步改 NOTE.md**，并在提交信息里说明——它是三人的共同基准。
