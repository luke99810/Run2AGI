# scripts —— 工程脚本

owner：**A**（改动前知会全员——所有人都在用）

---

## 状态

| 脚本 | 做什么 | 谁在用 | 状态 |
|---|---|---|---|
| `gen_types.py` | ★ `schemas/*.json` → `contracts/python/codentum_contracts/state.py` | CI + 所有人 | ✅ |
| `gen_contract_tests.py` | schema + 固件 → `tests/contract/`（105 用例） | CI + 所有人 | ✅ |
| `validate_fixtures.py` | 固件的 schema 校验 + **八项交叉检查** | CI | ✅ |
| `check_boundaries.py` | ★ 把 `boundaries.yaml` 从约定变成强制 | CI | ✅ |
| `secret_scan.py` | ★ 扫工作区 + **git 历史**，无跳过开关 | ★ **提交前必跑** | ✅ |
| `lib/schema.py` | 共享的 JSON Schema 载入 / $ref 解析 / 校验 | 上述脚本 | ✅ |
| `lib/yaml_lite.py` | boundaries.yaml 的 YAML 子集解析 | check-boundaries | ✅ |
| `snapshot.py` | 从真实运行导出 golden-state 快照 | A、C | ⬜ 待写 |

```bash
make verify           # 全部
make verify-offline   # ★ pip install -e ".[dev]" 之前就能跑的那部分
make gen              # gen:types + gen:contract-tests
make gen-check        # 只校验不写（CI 用）
```

> ⚠️ **`package.json` 里只登记已经存在的脚本。**
> 登记一个还不存在的文件，`make verify` 会以一个看不懂的 ENOENT 失败——
> 而"验证命令本身是坏的"比"没有验证命令"更糟：**它让人不再相信绿灯。**
> 新脚本写完再往 `scripts` 段里加。

> ⚠️ **`git init` 之前，两项检查处于「无法执行」而不是「通过」**：
> `check-boundaries` 的 I3 契约冻结、`secret-scan` 的历史扫描。
> 两个脚本都会显式打印这一点——**静默跳过的检查等于骗人。**

---

## gen-types 的三条实现约束

写在脚本头部，这里只提最要紧的一条：

**★ 输出必须确定性——同样的 schema 逐字节生成同样的文件。**

所以脚本里没有 `Date`、没有随机、也不依赖 `readdir` 顺序：要生成哪些 schema、按什么顺序，由脚本里的 `PLAN` 显式列出。

**漏登记一份 schema 会直接报错，不会自动兜底。** 这是刻意的——按文件名自动排序意味着"新增一个 schema"会静默改变整个输出文件的顺序，让 `gen:check` 在一个与本次改动完全无关的地方红，然后花半小时才想明白。

生成器认识三个 `x-` 关键字：`x-brand`（品牌字符串类型）、`x-typeName`（抽成具名类型）、`x-status` / `x-note`（忽略，只给人看）。

---

## gen-contract-tests 生成的是什么测试

不是「我们的固件对不对」（那是 validate-fixtures 的事），而是**任何实现都必须满足的契约行为**：

```
正例   真实固件必须【通过】
反例   删任一必填字段 / 加未声明字段 / 枚举填非法值 → 必须【被拒】
```

**★ 反例是从真实固件【变异】出来的，不是凭空造的。**

凭空造的实例很容易在别的地方就不合法了，于是测试通过的原因与它想测的东西无关——**这类"假绿灯"比没有测试更危险。**

找不到某类固件时脚本会**直接报错**，而不是生成一个空测试文件——空文件等于一个永远绿的假信号。

---

## validate-fixtures 为什么不直接用 ajv

因为除了 schema 校验，它还要做**八项 JSON Schema 表达不了的交叉检查**：

| 检查 | 为什么重要 |
|---|---|
| **I1**：running packet 的 `ownsPaths` 两两不相交 | 单写者，全案地基 |
| `acceptance.authoredBy ≠ role` | 自己给自己定验收即作弊 |
| `locks.heldBy` 必须是 running | **卡住即释放锁**——否则一个卡住的任务锁死整条流水线 |
| 依赖图无环 | 必须是 DAG |
| `graph.json` 节点 ↔ `packets/` 文件一致 | 对不上说明状态已经不自洽 |
| **I6 审计链首尾相接** | 断链即篡改，**这条不能只写在文档里** |
| `packet.evidence` 引用的证据存在 | 引用了不存在的证据 = 证据链是假的 |
| 无真实凭证 | 自己的产品自己先用 |

固件是这些不变量的第一道验证场。**连手工造的快照都违反不变量，说明不变量本身没想清楚。**

> 这套检查已经抓到过真问题：`blocked` 快照引用了一条不存在的证据（从 `mid-flight`
> 复制 packet 时漏带了对应的 evidence 文件）。不写这项检查，它会一直躺在那里。

---

## check-boundaries 校验什么

对应 `boundaries.yaml` 的 `invariants:` 段，外加几项结构检查：

| 检查 | 说明 |
|---|---|
| `I1-single-writer` | 两个**有主** module 的 `paths` 不得相交 |
| 生成物例外 | `owner: null` 可从有主区域里抠出**更具体**的一块（如 `contracts/python/codentum_contracts/state.py`），但不能反过来把有主区域包进去 |
| `packages/` 全覆盖 | 每个包必须**恰好一个** owner，0 个或 2 个都报错 |
| 路径存在性 | ★ **打错字的规则不会报错，只会永远不命中**——那比没有规则更糟 |
| `I3-contract-freeze` | 改 `packages/contracts/**` 的提交作者必须是 A（需 git） |
| `frozen_at` | 填了就必须是合法日期 |

**这个脚本让路径独占从"约定"变成"被强制"。**

没有它，`boundaries.yaml` 只是一份君子协定——而这套系统全部的论点就是**约定靠不住，约束才可靠**。我们不能在自己的仓库里违反自己的核心主张。

### 为什么自己写 YAML 解析（`lib/yaml_lite.py`）

为了让它能在 `pip install -e ".[dev]"` 之前跑——边界最容易在"还没来得及装依赖"的那种时刻被破坏。

**遇到不认识的语法直接抛错，不猜、不跳过。** 一个静默猜错的 YAML 解析器比没有解析器危险得多：它会让 check-boundaries 报告一个与文件内容无关的结论。

---

## 硬约束

1. **脚本必须幂等。** 跑两次和跑一次结果相同。
2. **不许调用模型。** 工程脚本要在 CI 里确定性地跑，不能有非确定性输出。
3. **★ 校验类脚本零运行时依赖。** `gen-types` 与 `validate-fixtures` 刻意不引 ajv 等库——
   它们要在 `pip install -e ".[dev]"` 之前就能跑，否则第一天拉下仓库的人没法确认自己拿到的是一份自洽的契约。
4. **`secret-scan` 没有跳过开关。** 见 `packages/delivery/README.md`——**一旦可豁免，它在最需要它的那天一定会被豁免。**
5. **失败要给出可操作的信息。**
   「校验失败」没用，「生成物与 schema 不一致，跑 `python scripts/gen_types.py`，★ 不要反过来改生成物」才有用。
