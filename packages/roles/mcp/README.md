# MCP 服务与第三方应用

本目录的每个 JSON 是一个 MCP server 的接入配置。

**引擎需要显式指向本目录才会读它：**

```bash
python -m codentum_engine --project-root <项目> --mcp-config-dir packages/roles/mcp
```

指向之后，凡是 `enabled: true` 且凭据齐全的都会**由引擎连一次**，
工具进入主 Agent 的工具面，所有 packet 共享（见 [ADR-0009](../../../docs/adr/0009-MCP-连接点归属与共享工具箱.md)）。

★ 不给 `--mcp-config-dir` 就完全不接 MCP，内置工具照常可用。
  默认关是有意的：可启动的 server 靠 npx 拉起来，要几秒、要网络、
  缺凭据的还会失败 —— 不该给每次引擎启动加一段不可控的等待。

★ 连接结果与**被跳过的条目及原因**都写在 `<state-dir>/mcp/connections.json`。
  没有它的话，「目录写错了」「配置全是关的」「连上了但模型没调用」
  三种情况看起来完全一样。

## 分工

| 层 | 归属 | 接入方式 |
|---|---|---|
| **Skill** | 能力抽象 | 共享，跨角色跨项目复用 |
| **MCP / 第三方应用** | 工具连接 | **主 Agent 接一次**，工具自动可用 |

新增一个第三方应用 = 加一个 JSON，**不改任何代码**。

---

## 现有目录

### 默认开启

| id | 用途 | 凭据 | 实测 |
|---|---|---|---|
| `playwright` | 端到端测试、浏览器自动化 | **无需** | ✅ 连接成功，24 个工具 |

★ 目录里**唯一**默认开启的 —— 因为它是唯一零凭据的。

### 默认关闭：会绕过本项目不变量的三个

| id | 包 | 风险 | 绕过什么 |
|---|---|---|---|
| `git` | `@cyanheads/git-mcp-server` | **R3** | 控制平面的状态机与 worktree 隔离 |
| `filesystem` | `@modelcontextprotocol/server-filesystem` | R2 | 工作区边界（内置 `write_file` 的路径穿越检查） |
| `browser` | `@modelcontextprotocol/server-puppeteer` | R1 | 无，但与 `playwright` 完全重叠 |

★ `git` 是本目录风险最高的：它能 commit / merge / 切分支，
  而并行 packet 的 worktree 隔离与状态转移都建立在
  「**只有 ReconcileLoop 动 git**」这个前提上。
  开它等于让 Agent 和控制平面抢方向盘。只读用途（diff / log）是安全的。

★ `filesystem` 的作用域**完全由 args 最后一项决定**，
  而内置 `write_file` 有 `_resolve_inside` 拦路径穿越 —— 这个 server 没有。

### 声明式清单（不启动）

| id | 用途 | 传输 |
|---|---|---|
| `agentteams` | Team-mode 编排 | http（尚无客户端，只作能力投影） |

### 第三方应用（使用者自行配置凭据）

| id | 用途 | 需要的凭据 |
|---|---|---|
| `github` | 仓库、Issue、PR、提交历史 | `GITHUB_PERSONAL_ACCESS_TOKEN` |
| `sentry` | 线上错误追踪 | `SENTRY_ACCESS_TOKEN` |
| `postgres` | 数据库只读查询与 schema | `POSTGRES_CONNECTION_STRING` |
| `notion` | 需求文档、技术方案 | `NOTION_TOKEN` |
| `feishu` | 文档、消息、日程、审批 | `APP_ID` / `APP_SECRET` |

★ 更多样例见 `../mcp-examples/`（不参与默认加载）。

---

## 启用一个应用（三步）

**1. 申请凭据** —— 每个配置的 `credentialHowTo` 字段写了去哪申请。

**2. 提供凭据**，二选一：

```bash
# 方式 A：环境变量（推荐——凭据不进仓库）
export GITHUB_PERSONAL_ACCESS_TOKEN=xxx
```

```jsonc
// 方式 B：写进配置的 env 字段
// ★ 注意：这样凭据会进入版本库，仅适合本机试验
{ "env": { "GITHUB_PERSONAL_ACCESS_TOKEN": "xxx" } }
```

**3. 把 `enabled` 改为 `true`。**

引擎下次启动即自动连接。缺凭据时**不会启动进程**，而是如实报告缺哪个变量：

```
✗ GitHub（github）：未配置凭据：GITHUB_PERSONAL_ACCESS_TOKEN
  （设为环境变量或写入配置的 env 字段）
```

---

## 配置字段

```jsonc
{
  "schemaVersion": 1,
  "id": "github",                    // 唯一标识，同时是工具名前缀
  "name": "GitHub",
  "category": "third-party-app",
  "purpose": "代码托管：仓库、Issue、PR、提交历史",

  "transport": "stdio",              // 目前只有 stdio 有真实客户端
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-github"],
  "env": {},

  "enabled": false,                  // ★ 默认关闭
  "requiresEnv": ["GITHUB_PERSONAL_ACCESS_TOKEN"],
  "credentialHowTo": "GitHub → Settings → Developer settings → …",
  "docs": "https://…",

  "status": "disconnected",          // ★ 如实标注，不预设为已连接
  "authentication": "missing",
  "tools": []                        // ★ 刻意留空，见下
}
```

### 三条约定

**① `tools` 刻意留空。**
工具清单由 server 在 `tools/list` 时提供。预先罗列会在 server 版本
变化后变成谎报——而**没有任何测试会因此变红**。

**② `status` 如实写 `disconnected`。**
未配凭据的服务不该显示为可用。界面区分「已声明」与「已连接」，
这个区别正是它有价值的地方。

**③ 工具名带 `id` 前缀。**
GitHub 与 GitLab 都有 `create_issue`；不加前缀会静默覆盖，
而模型看到的还是那一个名字——**它不会知道自己调的是哪一个**。

---

## 新增一个应用

1. 确认包名**真实存在**：`npm view <包名> version`
   ★ 这一步不能省。编一个不存在的包名，使用者会配半天连不上，
   而错误信息指向的方向完全不对。
2. 按上面的字段写一个 JSON，`enabled` 设 `false`
3. 跑 `pytest packages/roles/tests -q` 确认通过 schema 校验

### 加入前先回答一个问题

> **如果模型误调用了这个工具，后果可逆吗？**

不可逆的工具（写数据库、发消息、动资金）需要人工确认点，
而不只是一个 `enabled: true`。这类应用参见 `../mcp-examples/alipay.json`
的标注方式。
