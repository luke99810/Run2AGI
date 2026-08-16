# Codentum Windows 打包与 Gitee Release

## 目标产物

```text
release/
├── Codentum-Setup.exe
├── Codentum-Setup.exe.sha256
├── THIRD_PARTY_LICENSES.txt
└── RELEASE_NOTES.md
```

Electron 使用 NSIS 完整安装包。Python sidecar 和真实 A/B 引擎分别使用 PyInstaller `onedir`，通过 electron-builder `extraResources` 放入安装目录的 `resources/python/`。用户电脑不需要 Node 或 Python。安装后的 sidecar 只在桌面端绑定真实项目后自动发现同级 `codentum-engine`，不会在安装目录生成运行状态。

## 开发与生产路径

- 开发：显式环境变量或仓库构建目录。
- 打包：`path.join(process.resourcesPath, 'python', 'codentum-sidecar', 'codentum-sidecar.exe')`。
- 不允许从当前工作目录猜路径。
- sidecar 启动后先完成 protocol/capabilities 握手，再开放任何命令。

## 发布顺序

1. Python unit/contract tests。
2. PyInstaller engine/sidecar build + 两个二进制各自的 JSONL smoke test。
3. desktop typecheck/unit/build/Electron screenshot tests。
4. electron-builder NSIS。
5. 安装后 smoke test：启动、握手、项目读取、关闭、卸载。
6. secret scan：先扫描源码与完整 Git history；再解开 `app.asar` 并扫描其中的文本、安装目录中的文本资源、日志样本与 Release notes。二进制 `.exe/.dll/.pyd` 不做正则文本扫描，以 SHA-256、来源锁定和代码签名门禁覆盖。
7. 生成 SHA-256 和第三方许可证清单。
8. 对生产发布执行 Authenticode 签名门禁；证书仅来自本机或 CI Secret。
9. 创建 Gitee Release 并上传附件。

其中第 5 步执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  packages/delivery/codentum_delivery/cold_start/verify-installer.ps1 `
  -InstallerPath packages/desktop/release/Codentum-Setup.exe
```

该脚本要求真实 A/B 引擎握手为 `connected=true`。若当前安装包只含 sidecar 网关，脚本会
明确失败，不能用 `--self-test` 的绿灯替代。

第 6 步在仓库 secret scan 之外还要执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  packages/delivery/codentum_delivery/packaging/scan-packaged-app.ps1 `
  -AppDirectory packages/desktop/release/win-unpacked
```

Gitee PAT 只能来自本机凭据或 CI Secret，不写入仓库、日志、安装包或命令历史。公开发布必须通过 Windows 代码签名门禁：

```powershell
npm --prefix packages/desktop run release:verify-signature
```

证书通过 electron-builder 支持的 `CSC_LINK` / `CSC_KEY_PASSWORD` 等本机或 CI Secret 注入。未签名构建只能作为本地开发预览，不得上传为公开正式 Release；不能用临时自签名证书冒充可信发布签名。
