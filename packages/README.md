# packages/ —— 模块地图

六个 package，**每个只有一个 owner**（见根目录 `boundaries.yaml`）。

---

## 依赖方向

```
                    contracts          A   ★ 所有人只读，只有 A 能写
                  ┌────┴────┬─────────┬──────────┐
                  ↓         ↓         ↓          ↓
          control-plane  harness   delivery   desktop
              A            B          C          C
                           ↓
                         roles
                           B
```

**依赖是单向的，不允许反向。** 具体地：

| 规则 | 为什么 |
|---|---|
| `contracts` 不依赖任何东西 | 它是根。依赖了就会循环 |
| `control-plane` 不依赖 `harness` | 控制平面必须能在没有执行体的情况下推进状态、跑测试 |
| `harness` 不依赖 `control-plane` | ★ 执行体不该知道调度逻辑——知道了就会试图迎合它 |
| `desktop` 只读，不写系统状态 | Console 挂掉系统照常跑 |
| `delivery` 不依赖 `desktop` | 交付链路要能在无 GUI 的 CI 里跑通 |

> **`control-plane` 与 `harness` 互不依赖，只通过 `contracts` 通信。**
> 这不是洁癖——它是"控制平面零 LLM / 执行平面模型驱动"这条分界的代码体现。
> 一旦互相 import，两个平面就在实现上黏死了，风险分配的意义随之消失。

---

## 六个 package

| package | owner | 一句话 | LLM |
|---|---|---|---|
| [`contracts/`](contracts/) | A | 所有跨模块的类型、schema、接口签名 | ✗ |
| [`control-plane/`](control-plane/) | A | 调度、锁、准入、门禁、预算——确定性代码 | ✗ **零 LLM** |
| [`harness/`](harness/) | B | 单次执行的受控外壳：上下文装配、工具面、检查点 | 中段有 |
| [`roles/`](roles/) | B | 11 个角色的 RoleSpec、提示词、Skill | 中段有 |
| [`delivery/`](delivery/) | C | Provisioning、凭证扫描、打包、冷启动复现 | ✗ |
| [`desktop/`](desktop/) | C | Electron 控制台——纯读端 | ✗ |

**六个 package 里只有两个碰模型。** 这是刻意的：不可恢复的错误交给确定性代码，可恢复的错误才交给模型。

---

## 三条硬约束（Agent 读这段）

1. **不要为了方便而跨 package 直接 import 内部实现。** 只能 import 对方通过 `contracts` 暴露的类型与接口。
2. **不要在 `control-plane/` 或 `delivery/` 里调用任何模型 API。** 这两处一旦引入模型，"错误不可恢复的地方用确定性代码"这条原则就破了。
3. **需要改 `contracts/` 时，停下来提 ContractChangeRequest。** 不要自己改，也不要在自己的 package 里另建一份"临时类型"绕过去——后者更糟，它会静默漂移。

---

## 每个 package 的 README 写了什么

统一五段，Agent 和人都按这个读：

```
职责       —— 做什么，以及明确不做什么
归属       —— owner，谁评审
依赖       —— 依赖谁，被谁依赖
完成定义   —— ★ 机器可判定的，不是"做得好"
硬约束     —— 违反了就是设计问题，不是实现问题
```

> "完成定义"必须机器可判定。
> "看板渲染得好" ✗ ——"在 3 个 golden-state 快照上渲染结果与预期截图一致" ✓
