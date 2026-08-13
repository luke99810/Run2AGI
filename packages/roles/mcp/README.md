# MCP 服务与第三方应用

本目录的每个 JSON 是一个 MCP server 的接入配置。
**引擎启动时读取本目录，凡是 `enabled: true` 且凭据齐全的都会自动连接，
其工具进入主 Agent 的工具面。**

## 分工

| 层 | 归属 | 接入方式 |
|---|---|---|
| **Skill** | 能力抽象 | 共享，跨角色跨项目复用 |
| **MCP / 第三方应用** | 工具连接 | **主 Agent 接一次**，工具自动可用 |

新增一个第三方应用 = 加一个 JSON，**不改任何代码**。

---

## 现有目录

### 内置（无需外部凭据）

| id | 用途 | 传输 |
|---|---|---|
| `filesystem` | 本地文件读写 | stdio |
| `git` | 版本控制 | stdio |
| `browser` | 浏览器 | 声明式（尚无运行时） |
| `agentteams` | Team-mode 编排 | 声明式（http，尚无客户端） |

### 第三方应用（使用者自行配置凭据）

| id | 用途 | 需要的凭据 |
|---|---|---|
| `github` | 仓库、Issue、PR、提交历史 | `GITHUB_PERSONAL_ACCESS_TOKEN` |
| `playwright` | 端到端测试、浏览器自动化 | **无需凭据** |
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
