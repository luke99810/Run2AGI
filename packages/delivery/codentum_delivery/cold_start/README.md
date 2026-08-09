# 冷启动门禁

在 A/B 引擎尚未提供时，可先验证安装布局、内置 sidecar、三份快照和桌面壳冷启动：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  .\codentum_delivery\cold_start\verify-installer-shell.ps1 `
  -InstallerPath ..\desktop\release\Codentum-Setup.exe
```

这不是完整发布门禁；它会明确打印提示，不能代替下方的真实引擎验证。

`verify-sidecar.ps1` 对打包后的 sidecar 执行真实 JSONL 握手和优雅退出。它默认要求
A/B 引擎 `connected=true`；只打包了网关、没有引擎时会明确失败。

`verify-installer.ps1` 在系统临时目录静默安装 NSIS 产物，检查桌面程序和 sidecar 布局，
执行上述真实引擎握手，启动桌面程序，并在结束时卸载和清理。它不是“检查文件存在就通过”
的占位脚本。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\codentum_delivery\cold_start\verify-installer.ps1 `
  -InstallerPath ..\desktop\release\Codentum-Setup.exe
```

若当前尚无可打包的 A/B 引擎，使用 `codentum-sidecar.exe --self-test` 只能证明网关自身可启动；
不能替代本门禁，也不能据此声明 Agent 闭环可用。
