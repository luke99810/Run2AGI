[CmdletBinding()]
param(
    [Parameter()]
    [string]$PythonExecutable = "python"
)

$ErrorActionPreference = "Stop"
$PackagingRoot = $PSScriptRoot
$DeliveryRoot = (Resolve-Path (Join-Path $PackagingRoot "..\..")).Path
$EntryPoint = Join-Path $DeliveryRoot "codentum_delivery\sidecar.py"
$DistRoot = Join-Path $DeliveryRoot "dist"
$WorkRoot = Join-Path $DeliveryRoot "build\pyinstaller"
$SpecRoot = Join-Path $DeliveryRoot "build"
$ExpectedExecutable = Join-Path $DistRoot "codentum-sidecar\codentum-sidecar.exe"
$RequirementsFile = Join-Path $DeliveryRoot "requirements-build.txt"

if (-not (Test-Path -LiteralPath $EntryPoint -PathType Leaf)) {
    throw "Sidecar entry point is missing: $EntryPoint"
}

$Version = & $PythonExecutable -c "import sys; print('.'.join(map(str, sys.version_info[:3]))); raise SystemExit(sys.version_info < (3, 11))"
if ($LASTEXITCODE -ne 0) {
    throw "Python 3.11 or newer is required to build the sidecar."
}
Write-Host "Using Python $Version"

$PinnedLine = Get-Content -LiteralPath $RequirementsFile | Where-Object { $_ -match '^pyinstaller==[0-9]' } | Select-Object -First 1
if ($null -eq $PinnedLine) {
    throw "requirements-build.txt must pin PyInstaller with an exact version."
}
$ExpectedPyInstallerVersion = ($PinnedLine -split '==', 2)[1].Trim()
$InstalledPyInstallerVersion = [string](& $PythonExecutable -c "import PyInstaller; print(PyInstaller.__version__)" 2> $null)
$PyInstallerCheckExitCode = $LASTEXITCODE
$InstalledPyInstallerVersion = $InstalledPyInstallerVersion.Trim()
if ($PyInstallerCheckExitCode -ne 0) {
    throw "PyInstaller is not installed. Install the approved build dependency, then rerun this script."
}
if ($InstalledPyInstallerVersion -ne $ExpectedPyInstallerVersion) {
    throw "PyInstaller version mismatch: expected $ExpectedPyInstallerVersion, found $InstalledPyInstallerVersion."
}
Write-Host "Using PyInstaller $InstalledPyInstallerVersion"

New-Item -ItemType Directory -Force -Path $DistRoot, $WorkRoot, $SpecRoot | Out-Null

& $PythonExecutable -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --name codentum-sidecar `
    --paths $DeliveryRoot `
    --distpath $DistRoot `
    --workpath $WorkRoot `
    --specpath $SpecRoot `
    $EntryPoint
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE."
}

if (-not (Test-Path -LiteralPath $ExpectedExecutable -PathType Leaf)) {
    throw "PyInstaller reported success but the expected executable is missing: $ExpectedExecutable"
}

& $ExpectedExecutable --self-test
if ($LASTEXITCODE -ne 0) {
    throw "The packaged sidecar failed its dependency-free self-test."
}

$HashStream = [System.IO.File]::OpenRead($ExpectedExecutable)
try {
    $HashBytes = [System.Security.Cryptography.SHA256]::Create().ComputeHash($HashStream)
    $Hash = ([System.BitConverter]::ToString($HashBytes) -replace "-", "").ToLowerInvariant()
}
finally {
    $HashStream.Dispose()
}
$HashFile = "$ExpectedExecutable.sha256"
Set-Content -LiteralPath $HashFile -Encoding ascii -NoNewline -Value "$Hash  codentum-sidecar.exe"

Write-Host "Sidecar onedir build verified: $ExpectedExecutable"
Write-Host "SHA-256: $Hash"
