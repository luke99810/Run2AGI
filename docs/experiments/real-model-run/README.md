# 真实模型跑通一个 WorkPacket —— 首次（2026-08-10）

这个目录是**一次真实运行的原始证据**，不是示例，不是构造的样本。
文件直接从 worker 的证据目录复制出来，未编辑。

复现：

```bash
# 需要百炼 Key（任一）：DASHSCOPE_API_KEY / BAILIAN_API_KEY / QWEN_API_KEY
python -m pytest tests/e2e/test_real_model_execution.py -q
```

---

## 这一轮发生了什么

| 项 | 值 |
|---|---|
| 模型 | `qwen-coder-plus-1106`（RoleSpec `coder` 的默认路由） |
| Provider | 百炼 OpenAI-compatible |
| 会话 | `openai-compatible-8ebfe62034dc4ba7a126c8fbb7c591b6` |
| stop_reason | `end` |
| 用量 | input 290 · output 151 · cached 0 |
| 记账 | `cost_cny = 0.0`（**unknown pricing —— 不作为成本证据**，见待办 7d） |
| prompt digest | `sha256:70df196b…` |
| packet 终态 | `accepted` |
| 验收依据 | `file:model/result.json`（非 `sys:` 前缀，是真实证据） |

链路走完了：`pending → ready → running → review → accepted`，
模型被真的调用、用量真的落盘、产出真的被当作验收依据。

**这是「真实模型在 ReconcileLoop 内跑完一个 packet」的第一次。**
在此之前，模型网关的 20 项单测全部使用假 gateway，真实 API 只跑过手工 smoke
（只证明「能连上」）。

---

## ★ 但这一轮同时暴露了两个缺陷

打开 `response.txt` 看模型实际返回了什么：

> ### Blocker Report
>
> **Blocker Description:**
> The visible context does not provide any specific details about the task
> or the changes that need to be made. There are no files or instructions available…

**模型是对的。** 看 `prompt-user.md` —— 实际发出去的 prompt 里
`Visible Context: (none)`，**全文没有一句「要做什么」**。
而 prompt 结尾恰好写着 "Prefer explicit blockers over silent assumptions
when visible context is insufficient"。模型严格照做了。

### 缺陷一：契约里没有「要做什么」这个字段

`WorkPacket` 的全部字段：
`id / kind / state / role / ownsPaths / readsPaths / deps / acceptance /
budget / routing / attempts / evidence / provenance`。

**没有任务描述。** 任务意图只能靠 `ContextBundle` 注入，而它是可选的 ——
没配 `context_loader` 就是空。

为什么之前没发现：`TestRealExecution`（command_runner 那条）的 worker 是一段
写死的 Python 脚本，**它根本不需要知道任务是什么**。
换成真模型，缺口立刻现形。

> 这是「用假替身测出来的绿灯」的又一个样本：替身不需要的东西，测试也测不出缺。

### 缺陷二：worker 自陈干不了，系统照样验收

`stop_reason=end` 且没有 tool_calls → runner 判 `status=completed` →
`WorkerCompleted` → 验收看到一条非 `sys:` 的真实证据 → `accepted`。

**于是「我做不了这个任务」这份报告，被当成了交付物。**

这正是 `docs/项目进展与记忆.md` §十五 推论 2 说的那件事：
**「可判定」不等于「判得出差别」。** 一条永远返回 true 的验收谓词也是机器可判定的。

08-09 修掉的是「拿控制面自己的簿记当证据」，08-10 上午修掉的是「门禁层同一个洞」，
而这一条更深一层：**证据是真的，内容却说的是失败。**
前两个能靠前缀判定解决，这一个不能。

### 现状

缺陷二由 `test_blocker_report_should_not_be_accepted` 钉住，
用 `xfail(strict=True)` —— 现在预期失败，**修好之后它会变红**，提醒摘掉 xfail。
不用普通断言，是因为那会把缺陷写成「预期行为」。

缺陷一涉及**已冻结的契约**（`frozen_at = 2026-08-02`），
按 I3 的规矩需要三人同意 + 新 ADR + 走变更窗口，**不由一个人直接改**。
已记为待发起 ADR-0007，见 `docs/项目进展与记忆.md` 待办 22。

---

## 文件清单

| 文件 | 是什么 |
|---|---|
| `result.json` | runner 写的执行结果，验收依据就是它 |
| `usage.json` | token 用量与记账字段 |
| `response.txt` | 模型原文（那份 blocker 报告） |
| `tool_calls.json` | 工具调用（空 —— ModelGatewayRunner 目前是 one-shot，没有工具循环） |
| `prompt-user.md` | **实际发给模型的 prompt**，缺陷一的直接证据 |
| `packet-final.json` | 跑完落盘的 packet，可看到终态与证据引用 |
