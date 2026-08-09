# Codentum delivery（角色 C）

这里负责 Electron 与真实 Python 引擎之间的可交付边界。代码不调用模型、不写控制平面
状态，也不把“网关启动成功”冒充成“Agent 引擎可用”。

## 已实现

- `codentum_delivery/sidecar.py`：protocol v1 UTF-8 JSONL 网关。
- `engine_proxy.py`：用显式 argv（`shell=False`）拉起外部 A/B 引擎，持续消费 stderr，
  支持请求超时、关联响应和优雅退出。
- `gateway.py`：严格能力握手、项目绑定、revision 检查、会话内 `commandId` 幂等及结构化拒绝回执。
- `packaging/build-sidecar.ps1`：PyInstaller `onedir` 构建并执行打包后二进制自测。
- `secret_scan/`：同时扫描工作区和所有可达 Git blob；历史不可读时门禁失败。
- `provisioning/`：只解析非敏感表单元数据并在内存中校验提交；不保存凭证，也不伪造连通成功。
- `cold_start/`：真实安装、sidecar 握手、桌面启动、关闭和卸载验证脚本。

## 真实引擎接入

sidecar 不内置假的 Agent。A/B 提供可打包 JSONL 引擎后，以 JSON 数组配置进程 argv：

```powershell
$env:CODENTUM_ENGINE_COMMAND_JSON='["C:\\Program Files\\Codentum\\engine\\codentum-engine.exe","--stdio"]'
```

禁止传 shell 命令字符串。外部引擎必须实现相同的请求/响应信封及 `handshake`、`command`、
`shutdown`。连接成功的握手必须包含绝对、canonical 的 `projectRoot`；每条命令的
`payload.projectRoot` 必须与之匹配。未配置、无法启动、握手不兼容时，sidecar 仍可报告状态，但返回：

```json
{
  "connected": false,
  "capabilities": {
    "requirements": false,
    "planConfirmation": false,
    "pauseAtSafePoint": false,
    "resume": false,
    "stop": false,
    "keepMemory": false,
    "forkFromCheckpoint": false,
    "appendPrompt": false,
    "insertModule": false
  }
}
```

因此桌面端必须禁用对应按钮。网关不会生成假的 `accepted`/`applied` 回执。

### 幂等与超时

- 同一 sidecar 会话中，相同 `commandId` + 相同内容直接返回缓存回执，不再次调用引擎。
- 相同 `commandId` + 不同内容返回 `idempotency_conflict`。
- 达到幂等缓存上限后拒绝新命令并要求重启，不淘汰旧 ID 后冒险重复执行。
- 超时后返回 `engine_timeout_reconcile_authoritative_state`，且不自动重试；引擎可能已经执行，
  桌面端必须重新读取权威状态。
- 跨 sidecar 重启的持久幂等必须由 A/B 引擎实现，C 网关不能代写控制平面状态。

## 开发和验证

以下命令不需要安装项目依赖：

```powershell
python packages/delivery/codentum_delivery/sidecar.py --self-test
python -m unittest discover -s packages/delivery/tests -p "test_*.py" -v
```

secret-scan 是不可豁免门禁，没有 `--skip` 或 `--force`：

```powershell
$env:PYTHONPATH='packages/delivery'
python -m codentum_delivery.secret_scan --root .
```

角色 C 的单元测试位于 `packages/delivery/tests`。按团队约定，secret-scan 的最终验收测试仍由
A 编写和持有；C 的自测不能替代 A 的 QA-first 验收。

构建 sidecar 前先安装锁定的构建依赖：

```powershell
python -m pip install -r packages/delivery/requirements-build.txt
```

然后构建：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  packages/delivery/codentum_delivery/packaging/build-sidecar.ps1
```

产物路径与 Electron `extraResources` 对齐：
`packages/delivery/dist/codentum-sidecar/codentum-sidecar.exe`。

## 当前不能宣称完成的事项

- A/B 尚未交付真实可打包引擎时，所有执行能力为 `false`。
- Provisioning 目前没有凭证持久化；必须接入 Windows Credential Manager 等 OS 凭据库后才可做。
- 连通性状态固定为 `not_available`，直至 A/B connector 提供真实探测能力。
- `Codentum-Setup.exe` 只有通过 `cold_start/verify-installer.ps1`（含真实引擎握手）后，
  才能标记为可发布。sidecar 的 `--self-test` 不能替代该门禁。

完整顺序见 [Windows 打包与 Gitee Release](docs/PACKAGING_RELEASE_RUNBOOK.md)。
