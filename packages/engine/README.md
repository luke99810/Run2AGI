# packages/engine —— 装配点

owner: **A** ｜ 见 [ADR-0007](../../docs/adr/0007-引擎入口归属与装配点.md)（状态：提议，待 B/C 确认）

把 C 的 delivery 协议接到 A 的 `ReconcileLoop` 上的那个进程。

在它之前，`engine_proxy` 唯一对话过的「引擎」是
`packages/delivery/tests/_fake_engine.py` —— 那个文件第一行写着
`never shipped as an engine`。

```
App.tsx → IPC → PythonEngineClient → SidecarGateway → JsonlEngineProxy
                                                            ↓
                                                   codentum_engine  ← 本包
                                                            ↓
                                        ReconcileLoop ← 控制平面（A）
                                                            ↓
                                        LocalWorkerRuntime ← Harness（B）
```

---

## 跑起来

```bash
# 1. 装配点自身的单测（不调模型，20 秒）
python -m pytest packages/engine/tests/ -q

# 2. 手动起一个引擎，用 stdin 喂协议（stdout 是协议通道，stderr 是日志）
export DASHSCOPE_API_KEY=...        # 没有 Key 时能力表会如实报 requirements=false
python -m codentum_engine --project-root /path/to/repo
```

桌面端那边按 JSON argv 配（`SidecarGateway` 永远不走 shell）：

```
CODENTUM_ENGINE_COMMAND_JSON=["python","-m","codentum_engine","--project-root","/path/to/repo"]
```

---

## 它做的三个选择

装配点自己**不做任何判定** —— 状态机、路径锁、门禁、预算、Guardian 全在
控制平面，工具面与模型调用全在 Harness。它只选默认值。而这三个默认值
比它写的代码更重要：

### 1. 四个安全组件显式全开

`ReconcileLoop` 的 `transition_table` / `gate_runner` / `budget_tracker` /
`guardian` **默认全是 `None`**。对库合理，对产品入口不合理。

08-10 的护栏消融实验量到：关掉之后 I1 冲突 8/8 放行、重试上限 8/8 放行、
全局预算 8/8 放行、门禁 32/32 放行。**「护栏关」不是人造对照组，那就是默认配置。**

例外是 `transition_table`，默认仍为 `None` —— 理由不是「嫌麻烦」，
见 `tests/test_role_transition_gap.py`：打开它会让 coder 的 packet 永远停在
review，因为 `_try_review_to_accepted` 拿 packet 自己的 role 去问
「谁能验收它」。表是对的，提问方式不对。这是控制平面的建模问题，
**不在装配层绕过**，改好之后那条测试会变红。

### 2. 能力表按「按下去会不会真的发生事情」报

假引擎把 9 个 capability 全报 `true`。照抄的话桌面端会显示一排点了没反应的
按钮，而不会有任何东西报错。这里只有 `requirements` 报 true；
**连它在没有模型 Key 时也报 false** —— 因为没有 runner 的话 packet 建出来
也不会执行，那是最难查的一种「成功」：命令被接受了，状态却永远不动。

### 3. `submit_requirement` 回 `accepted`，不回 `applied`

网关默认超时 8 秒，真实模型 packet 要跑 30～180 秒。
回 `applied` 等于在结果出来之前宣布结果。执行放后台线程，
桌面端通过监视 `.codentum/` 看进展。

---

## 它明确没有做的

| 缺什么 | 现在怎么处理 | 谁来补 |
|---|---|---|
| **Planner** | 一个需求 → **一个** packet，不是分解 | 写死一套假拆分演示更好看，但那是编的 |
| **Intake** | 占位验收署名按优先级退到存在的角色（当前 `qa`），日志留痕 | B 补 `intake` RoleSpec 后自动切回，有测试钉着 |
| `WorkPacket` 无任务描述字段 | 需求原文经 `context_loader` 进 `ContextBundle`（设计里本来就有的注入点） | 契约层缺陷仍在，走 ADR-0008 |
| worker 自陈干不了仍被验收 | **半修**：B 在 `ModelGatewayRunner` 加了确定性 blocker 检测，**显式**的「Blocker Report / 无法继续」现在会转成 `WorkerFailed`，引擎走同一条 runner 所以直接继承 | 剩下的一半仍未修：模型把代码写在回复正文里、`tool_calls` 为空、**一个文件都没创建**，照样 `accepted` ——「写了字」不等于「交了活」 |
| `ReconcileLoop` 没有公开的 `admit(packet)` | 本包直接写了私有字段，已记为待办 27 | A 的控制平面改动 |

---

## 文件

| 文件 | 是什么 |
|---|---|
| `session.py` | `runId` 与 `stateRevision` 的落盘 —— 重启不回退，否则网关会拒 |
| `intake.py` | 需求 → packet；需求原文存档；占位验收的署名选择 |
| `service.py` | 协议方法的实现体，**不碰 stdio**（所以可直接测） |
| `__main__.py` | stdio JSONL 循环。★ stdout 是协议通道，这个文件里不许出现 print |
