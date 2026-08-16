[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InstallerPath,

    [Parameter()]
    [ValidateRange(1, 120)]
    [int]$StartupTimeoutSeconds = 15
)

$ErrorActionPreference = "Stop"
$ResolvedInstaller = (Resolve-Path -LiteralPath $InstallerPath).Path
if (-not (Test-Path -LiteralPath $ResolvedInstaller -PathType Leaf)) {
    throw "Installer is missing: $InstallerPath"
}
if ((Get-Item -LiteralPath $ResolvedInstaller).Length -eq 0) {
    throw "Installer is empty: $ResolvedInstaller"
}

$TempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$InstallDirectory = [System.IO.Path]::GetFullPath(
    (Join-Path $TempRoot ("codentum-cold-start-" + [guid]::NewGuid().ToString("N")))
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
    $EnginePath = Join-Path $InstallDirectory "resources\python\codentum-engine\codentum-engine.exe"
    if (-not (Test-Path -LiteralPath $ApplicationPath -PathType Leaf)) {
        throw "Installed desktop executable is missing: $ApplicationPath"
    }
    if (-not (Test-Path -LiteralPath $SidecarPath -PathType Leaf)) {
        throw "Installed Python sidecar is missing: $SidecarPath"
    }
    if (-not (Test-Path -LiteralPath $EnginePath -PathType Leaf)) {
        throw "Installed A/B engine is missing: $EnginePath"
    }

    $ProjectRoot = Join-Path $InstallDirectory "cold-start-project"
    New-Item -ItemType Directory -Force -Path $ProjectRoot | Out-Null
    $SavedProjectRoot = $env:CODENTUM_PROJECT_ROOT
    $env:CODENTUM_PROJECT_ROOT = $ProjectRoot

    try {
        & (Join-Path $PSScriptRoot "verify-sidecar.ps1") `
            -SidecarPath $SidecarPath `
            -TimeoutSeconds $StartupTimeoutSeconds `
            -ProjectRoot $ProjectRoot
    }
    finally {
        if ($null -eq $SavedProjectRoot) {
            Remove-Item Env:CODENTUM_PROJECT_ROOT -ErrorAction SilentlyContinue
        }
        else {
            $env:CODENTUM_PROJECT_ROOT = $SavedProjectRoot
        }
    }

    $ApplicationProcess = Start-Process -FilePath $ApplicationPath -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds ([Math]::Min(5, $StartupTimeoutSeconds))
    if ($ApplicationProcess.HasExited) {
        throw "Installed desktop application exited during startup with code $($ApplicationProcess.ExitCode)."
    }
    Write-Host "PASS: installer layout, real engine task creation, and desktop startup verified."
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
        $Uninstaller = Start-Process `
            -FilePath $UninstallerPath `
            -ArgumentList "/S" `
            -WindowStyle Hidden `
            -Wait `
            -PassThru
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
