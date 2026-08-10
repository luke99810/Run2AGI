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
- `manager-console-before-worker.png`
- `manager-console-worker-request-sent.png`
- `manager-console-worker-response.png`

## Worker Creation Smoke

Date: 2026-08-10 23:58 CST

Goal: move beyond "the AgentTeams services open in a browser" and verify that the local manager substrate can create a Worker resource.

The browser-based Manager Console accepted the request text but showed a transient `Channel Console not found` message, so the deterministic verification path used the official AgentTeams CLI inside `agentteams-controller`.

Command:

```text
docker exec agentteams-controller agt create worker --name coder --runtime copaw --model qwen3.6-plus --identity 'Python development worker for Codentum local AgentTeams verification.' --wait-timeout 5m
```

Result caveat: the create command timed out waiting for the stricter Ready hook, but the resource and container reached Running:

```text
Error: worker/coder did not become ready within 5m0s (last status: phase=Running, state=running, message=backend=docker status=running)
```

Follow-up status checks:

```text
$ docker exec agentteams-controller agt get workers
NAME   PHASE    MODEL         TEAM  RUNTIME
coder  Running  qwen3.6-plus  -     copaw

$ docker exec agentteams-controller agt worker ensure-ready --name coder
worker/coder phase=Running
```

Worker status:

```json
{
  "name": "coder",
  "phase": "Running",
  "containerManaged": true,
  "state": "Running",
  "model": "qwen3.6-plus",
  "runtime": "copaw",
  "identity": "Python development worker for Codentum local AgentTeams verification.",
  "containerState": "running",
  "matrixUserID": "@coder:matrix-local.agentteams.io:18080",
  "roomID": "!CVKRikpAGdYOI9FlUJ:matrix-local.agentteams.io:18080",
  "message": "backend=docker status=running"
}
```

Running Worker container:

```text
agentteams-worker-coder  higress-registry.cn-hangzhou.cr.aliyuncs.com/agentteams/agentteams-copaw-worker:v1.2.2
```

Conclusion: AgentTeams local Worker resource creation is smoke-verified at the resource/container level. The Ready hook timeout remains a caveat and should not be described as full Team-mode integration.

## Local Secret Locations

The installer generated local credentials and saved them outside the repository:

- `~/agentteams-manager.env`
- `~/agentteams-install.log`

These files are local operation artifacts only and must not be committed.
