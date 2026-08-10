# AgentTeams / HiClaw Local Install Verification

Date: 2026-08-10 23:12 CST

Scope: local installation verification for the AgentTeams requirement. This proves the local Team-mode substrate can start on this Mac; it is not yet a Codentum `WorkerRuntime` adapter.

## Installer

- Source: official AgentTeams installer, `agentteams-install.sh`
- URL: `https://raw.githubusercontent.com/agentscope-ai/AgentTeams/main/install/agentteams-install.sh`
- SHA-256: `55557ffd695936d58aa267b0c20e4040da849db815deefb36a7a0f82690bb8ce`
- Mode: non-interactive manager install
- Provider: OpenAI-compatible Alibaba Model Studio workspace
- Model: `qwen3.6-plus`

The API key came from the user's local Alibaba Model Studio CSV and was passed only through the installer process environment. Do not commit `~/agentteams-manager.env` or `~/agentteams-install.log`.

## Result

Docker:

```text
Docker version 28.3.2
Docker Compose version v2.38.2-desktop.1
```

Running containers:

```text
agentteams-controller  higress-registry.cn-hangzhou.cr.aliyuncs.com/agentteams/agentteams-embedded:v1.2.2
agentteams-manager     higress-registry.cn-hangzhou.cr.aliyuncs.com/agentteams/agentteams-manager-copaw:v1.2.2
agentteams-dashboard   higress-registry.cn-hangzhou.cr.aliyuncs.com/agentteams/agentteams-dashboard:v1.2.2
```

HTTP checks:

```text
http://127.0.0.1:18088/#/login  status=200  Element Web
http://localhost:18001          status=200  Higress Console
http://localhost:13000/         status=200  AgentTeams Dashboard
http://127.0.0.1:18888          status=200  Manager console
```

Ports:

```text
127.0.0.1:18088 -> Element Web
127.0.0.1:18001 -> Higress Console
127.0.0.1:13000 -> AgentTeams Dashboard
127.0.0.1:18888 -> Manager console
```

## Screenshots

- `element-login.png`
- `higress-console.png`
- `agentteams-dashboard.png`

## Local Secret Locations

The installer generated local credentials and saved them outside the repository:

- `~/agentteams-manager.env`
- `~/agentteams-install.log`

These files are local operation artifacts only and must not be committed.
