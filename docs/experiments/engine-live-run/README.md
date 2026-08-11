# 桌面协议 → 真引擎 → 真模型：首次全链路（2026-08-11）

这个目录是**一次真实运行的原始产物**，直接从运行目录复制，未编辑。

复现：

```bash
# 需要百炼 Key（任一）：DASHSCOPE_API_KEY / BAILIAN_API_KEY / QWEN_API_KEY
python -m pytest tests/e2e/test_engine_live_model.py -q
```

入口是 `SidecarGateway`（C 的桌面端用的就是它），出口是 `.codentum/`。
中间第一次有了一个真的引擎 —— 在此之前 `engine_proxy` 唯一对话过的对象是
`packages/delivery/tests/_fake_engine.py`，那个文件自己写着
`never shipped as an engine`。

---

## 这一轮发生了什么

| 项 | 值 |
|---|---|
| 入口 | `SidecarGateway.dispatch` → JSONL → `python -m codentum_engine` |
| 需求 | 「在 workspace/ 下新建 subscriptions.py，实现 Subscription 数据类与 monthly_total 函数。」 |
| 模型 | `qwen-coder-plus-1106`（百炼 OpenAI-compatible，HTTP 200） |
| 用量 | input 353 · output 848 · cached 0 |
| 记账 | `cost_cny = 0.0`（**unknown pricing —— 不作为成本证据**，待办 7d） |
| 全程 | 约 24 秒（10:46:18 → 10:46:42） |
| packet 终态 | `accepted`，evidence = `file:model/result.json`（非 `sys:` 前缀） |
| 磁盘可见状态序列 | `pending → running → accepted`（**中间态真的落盘了**，见下） |

---

## ★ 与 08-10 那次的关键差别：模型这次知道要做什么

08-10 首次真实模型跑通时，实际发出去的 prompt 里是：

```
## Visible Context

(none)
```

于是模型回了一份 blocker 报告：「可见上下文里没有任何任务细节」—— **模型是对的**。
根因是 `WorkPacket` 契约里**没有任务描述字段**。

这一轮的 `prompt/user.md`（本目录内，未编辑）：

```
## Visible Context

### requirement:wp-5fe438a743b1

- path: requirements/wp-5fe438a743b1.json
- mode: full
- original_chars: 72

​```
在 workspace/ 下新建 subscriptions.py，实现 Subscription 数据类与 monthly_total 函数。
​```
```

模型的回应也随之从「我做不了」变成了直接开始实现（见 `response.txt`）。

**做法不是改契约。** 走的是 `LocalWorkerRuntime` 本来就有的 `context_loader`
注入点：需求原文落盘到 `.codentum/requirements/<packetId>.json`，
由引擎的 context loader 作为 `required=True` 的候选交给 Harness。

★ 契约层的缺陷**仍然存在**，仍然要走变更窗口（ADR-0008）。
这里解决的是「模型收不到任务」这个可运行性问题，不是那个契约缺陷本身。

---

## ★ 这一轮也不能被夸大：模型没有真的改文件

`tool_calls.json` 是**空的**。`ModelGatewayRunner` 目前是 one-shot、没有工具
循环 —— 模型把代码写在了回复正文里，没有任何文件被创建。

所以这一条证明的是：

- ✅ 桌面协议 → 引擎 → 控制平面 → 真模型，这条链路是通的
- ✅ 需求原文确实到达了模型
- ❌ **不证明「模型改了代码」** —— 那条判据由 `TestRealExecution`
  （command_runner）守着，两条合起来才是完整的 P0

### ★ 缺陷二现在是「半修」，说准很重要

08-10 记的缺陷二是：`stop_reason=end` + 无 tool_calls → runner 判 completed →
门禁看到一条真实证据 → `accepted`，于是「我做不了」被当成了交付物。

**B 在 08-10 修掉了其中一半**（`ModelGatewayRunner` 的确定性 blocker 检测：
`Blocker Report` / `cannot proceed` / `无法继续` 等前缀 → `WorkerFailed`）。
引擎走的是同一个 runner，所以**直接继承了这个修复，不需要自己再写一遍**。

**但另一半仍然存在，而且本轮就是它的样本**：模型没有报 blocker，
它把代码写在了回复正文里 —— `tool_calls` 为空，**一个文件都没有被创建**，
packet 仍然 `accepted`。

> 前一半能靠关键词判定解决：**说了「我做不了」**。
> 后一半不能：**什么都没说，只是什么也没做。**
> 判据得从「模型说了什么」换成「工作区里多了什么」——
> 那是设计决定，不是补丁。

原来那条 `xfail(strict=True)` 已由 B 改写：确定性判定移到
`packages/harness/tests/test_model_gateway_runner.py`（不依赖模型随机行为），
真实模型那条只在模型**本次确实**返回 blocker 时才检查「不得 accepted」，
否则跳过 —— 避免把模型的随机行为写成稳定判据。★ 这个改法是对的。

---

## 接线过程中当场发现并修掉的三个缺陷

这三个都不是设计问题，是**只有真的把两头接起来才会现形**的问题。

### 一、子进程继承了协议通道，git 卡死 240 秒

`LocalWorkerRuntime.__init__` 构造 `GitWorktreeManager`，后者跑
`git rev-parse --show-toplevel`。这个 git 子进程**继承了引擎的 fd 0**，
而 fd 0 正是桌面端发来的协议管道，主线程还在阻塞读它。实测：

```
10:31:02  已取得状态锁，开始装配
10:35:02  装配完成，开始 tick      ← 整整四分钟，无任何报错
10:35:24  模型返回
```

现象是「提交后四分钟没反应」，日志里一切正常。

**更严重的是静的那一面**：任何读 stdin 的子进程都会把桌面端发来的命令
从协议通道里读走 —— 命令消失，桌面端等到超时，引擎这边什么都没发生。
不报错、不可复现、没有测试会红。

修法在**入口层**：dup 出协议通道自己用，fd 0 换成 devnull。
不在 `worktree.py` 每个 `subprocess.run` 补 `stdin=DEVNULL` ——
那既越界（B 的路径），也只能堵住当下这几处。fd 0 只有一个，
在进程入口一次换掉才覆盖全部子进程。

修复后装配从 240 秒降到 **1.9 秒**。

### 二、执行过程对桌面端完全不可见

`ReconcileLoop.run_until_stable()` 内部连跑多轮，**只在最外层保存一次**。
于是一次真实模型调用的整段时间里，`.codentum/` 里的 packet 一直是初始状态，
最后一刻突然跳到终态。

一个卖点是「执行过程看得见」的产品，恰好在执行过程中什么都看不见。

`run_until_stable` 本身没错 —— 它是给测试用的（load → 跑完 → 断言终态）。
错的是产品入口照抄了它。**选择刷新策略正是装配点的职责。**
现在每轮 tick 都落盘，本轮实测磁盘上依次出现了 `pending → running → accepted`。

由 `packages/engine/tests/test_progress_is_observable.py` 钉住。
★ 那条测试的第一版是**空转的**：它断言「磁盘上出现过 ≥2 个不同状态」，
而起点和终点本来就是两个 —— 换回退化写法照样全绿。
改成数落盘次数之后才真的会红。（又一次「对照组配错了，实验照样出数」。）

### 三、Windows 上流编码两侧不一致

`JsonlEngineProxy` 按 UTF-8 解码管道，而 Windows 上 Python 的
stdout/stderr 默认跟随本地代码页（本机 GBK）。中文需求正文会变成一串
U+FFFD，**而协议结构完好、没有任何东西报错**。

这是本项目第三次踩「一个概念在两侧各写一遍」：
证据判据写两遍（门禁比兜底松）· EvidenceRef 路径分隔符两平台不一致 ·
现在是流编码。

---

## 文件清单

| 文件 | 是什么 |
|---|---|
| `requirement.json` | 引擎存下的需求原文与整份 payload（含桌面端塞的 taskId 等未知字段） |
| `prompt/user.md` → `user.md` | **实际发给模型的 prompt**，本轮最重要的一份 |
| `system.md` | 系统提示 |
| `response.txt` | 模型原文 |
| `result.json` | runner 写的执行结果，验收依据就是它 |
| `usage.json` | token 用量与记账字段 |
| `tool_calls.json` | 工具调用 —— **空的**，见上文「不能被夸大」 |
| `packet-final.json` | 跑完落盘的 packet |
| `engine-session.json` | runId 与 stateRevision（重启不回退靠它） |
| `manifest.json` / `0000.json` | Harness 的证据清单与检查点 |
