[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InstallerPath,

    [Parameter()]
    [ValidateRange(2, 30)]
    [int]$StartupTimeoutSeconds = 8
)

$ErrorActionPreference = "Stop"
$ResolvedInstaller = (Resolve-Path -LiteralPath $InstallerPath).Path
$TempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$InstallDirectory = [System.IO.Path]::GetFullPath(
    (Join-Path $TempRoot ("codentum-shell-smoke-" + [guid]::NewGuid().ToString("N")))
)
if (-not $InstallDirectory.StartsWith($TempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to install outside the system temporary directory."
}

$ApplicationProcess = $null
$SavedElectronRunAsNode = $env:ELECTRON_RUN_AS_NODE
try {
    Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
    $InstallerProcess = Start-Process `
        -FilePath $ResolvedInstaller `
        -ArgumentList @("/S", "/D=$InstallDirectory") `
        -WindowStyle Hidden `
        -Wait `
        -PassThru
    if ($InstallerProcess.ExitCode -ne 0) {
        throw "Silent installation failed with exit code $($InstallerProcess.ExitCode)."
    }

    $ApplicationPath = Join-Path $InstallDirectory "Codentum.exe"
    $SidecarPath = Join-Path $InstallDirectory "resources\python\codentum-sidecar\codentum-sidecar.exe"
    $FixtureRoot = Join-Path $InstallDirectory "resources\fixtures\golden-state"
    foreach ($RequiredPath in @($ApplicationPath, $SidecarPath, $FixtureRoot)) {
        if (-not (Test-Path -LiteralPath $RequiredPath)) {
            throw "Installed package is missing: $RequiredPath"
        }
    }
    $FixtureNames = @(Get-ChildItem -LiteralPath $FixtureRoot -Directory | Select-Object -ExpandProperty Name)
    foreach ($Expected in @("empty", "mid-flight", "blocked")) {
        if ($Expected -notin $FixtureNames) {
            throw "Installed package is missing fixture '$Expected'."
        }
    }

    & $SidecarPath --self-test
    if ($LASTEXITCODE -ne 0) {
        throw "Packaged sidecar fail-closed self-test failed with exit code $LASTEXITCODE."
    }

    $ApplicationProcess = Start-Process -FilePath $ApplicationPath -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds ([Math]::Min(5, $StartupTimeoutSeconds))
    if ($ApplicationProcess.HasExited) {
        throw "Installed desktop application exited during startup with code $($ApplicationProcess.ExitCode)."
    }
    Write-Host "PASS: installer layout, bundled sidecar, fixtures, and desktop shell startup verified."
    Write-Warning "This shell smoke is not the real-engine cold-start gate; run verify-installer.ps1 after A/B supplies the engine."
}
finally {
    if ($null -ne $ApplicationProcess -and -not $ApplicationProcess.HasExited) {
        $null = $ApplicationProcess.CloseMainWindow()
        if (-not $ApplicationProcess.WaitForExit(3000)) {
            Stop-Process -Id $ApplicationProcess.Id -Force
        }
    }
    $UninstallerPath = Join-Path $InstallDirectory "Uninstall Codentum.exe"
    if (Test-Path -LiteralPath $UninstallerPath -PathType Leaf) {
        $Uninstaller = Start-Process -FilePath $UninstallerPath -ArgumentList "/S" -WindowStyle Hidden -Wait -PassThru
        if ($Uninstaller.ExitCode -ne 0) {
            Write-Warning "Uninstaller returned exit code $($Uninstaller.ExitCode)."
        }
    }
    if (Test-Path -LiteralPath $InstallDirectory) {
        $Verified = [System.IO.Path]::GetFullPath($InstallDirectory)
        if (-not $Verified.StartsWith($TempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to clean a directory outside the system temporary directory."
        }
        Remove-Item -LiteralPath $Verified -Recurse -Force
    }
    if ($null -eq $SavedElectronRunAsNode) {
        Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
    }
    else {
        $env:ELECTRON_RUN_AS_NODE = $SavedElectronRunAsNode
    }
}
