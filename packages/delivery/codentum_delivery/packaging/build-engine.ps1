[CmdletBinding()]
param(
    [Parameter()]
    [string]$PythonExecutable = "python"
)

$ErrorActionPreference = "Stop"
$PackagingRoot = $PSScriptRoot
$RepositoryRoot = (Resolve-Path (Join-Path $PackagingRoot "..\..\..\..")).Path
$DeliveryRoot = Join-Path $RepositoryRoot "packages\delivery"
$EngineRoot = Join-Path $RepositoryRoot "packages\engine"
$RolesRoot = Join-Path $RepositoryRoot "packages\roles"
$EntryPoint = Join-Path $PackagingRoot "engine-entry.py"
$DistRoot = Join-Path $DeliveryRoot "dist"
$WorkRoot = Join-Path $DeliveryRoot "build\pyinstaller-engine"
$SpecRoot = Join-Path $DeliveryRoot "build\engine"
$ExpectedExecutable = Join-Path $DistRoot "codentum-engine\codentum-engine.exe"
$RequirementsFile = Join-Path $DeliveryRoot "requirements-build.txt"

if (-not (Test-Path -LiteralPath $EntryPoint -PathType Leaf)) {
    throw "Engine entry point is missing: $EntryPoint"
}

$Version = & $PythonExecutable -c "import sys; print('.'.join(map(str, sys.version_info[:3]))); raise SystemExit(sys.version_info < (3, 11))"
if ($LASTEXITCODE -ne 0) {
    throw "Python 3.11 or newer is required to build the engine."
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

foreach ($Module in @("pydantic", "langgraph", "openai", "anthropic")) {
    & $PythonExecutable -c "import importlib.util, sys; raise SystemExit(importlib.util.find_spec(sys.argv[1]) is None)" $Module
    if ($LASTEXITCODE -ne 0) {
        throw "Python runtime dependency '$Module' is missing. Install the project runtime dependencies before packaging."
    }
}

$PackageRoots = @(
    (Join-Path $RepositoryRoot "packages\contracts\python"),
    (Join-Path $RepositoryRoot "packages\control-plane"),
    (Join-Path $RepositoryRoot "packages\harness"),
    $RolesRoot,
    $DeliveryRoot,
    $EngineRoot
)

New-Item -ItemType Directory -Force -Path $DistRoot, $WorkRoot, $SpecRoot | Out-Null

$Arguments = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onedir",
    "--name", "codentum-engine",
    "--distpath", $DistRoot,
    "--workpath", $WorkRoot,
    "--specpath", $SpecRoot,
    "--hidden-import", "openai",
    "--hidden-import", "anthropic"
)
foreach ($Root in $PackageRoots) {
    $Arguments += @("--paths", $Root)
}
foreach ($Resource in @("specs", "prompts", "skills", "mcp")) {
    $Arguments += @("--add-data", "$(Join-Path $RolesRoot $Resource);$Resource")
}
$Arguments += $EntryPoint

& $PythonExecutable @Arguments
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE."
}
if (-not (Test-Path -LiteralPath $ExpectedExecutable -PathType Leaf)) {
    throw "PyInstaller reported success but the expected engine executable is missing: $ExpectedExecutable"
}

$SmokeRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("codentum-engine-smoke-" + [guid]::NewGuid().ToString("N"))
try {
    New-Item -ItemType Directory -Force -Path $SmokeRoot | Out-Null
    $Request = '{"id":"build-handshake","method":"handshake","params":{"protocolVersion":1}}' + "`n" +
        '{"id":"build-shutdown","method":"shutdown","params":{}}' + "`n"
    $Output = $Request | & $ExpectedExecutable --project-root $SmokeRoot --log-level WARNING
    if ($LASTEXITCODE -ne 0) {
        throw "The packaged engine failed its protocol smoke test."
    }
    $Responses = @($Output | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | ForEach-Object { $_ | ConvertFrom-Json })
    if ($Responses.Count -ne 2 -or $Responses[0].ok -ne $true -or $Responses[0].result.connected -ne $true) {
        throw "The packaged engine did not return a connected protocol-v1 handshake."
    }
    if ($Responses[1].ok -ne $true) {
        throw "The packaged engine did not acknowledge shutdown."
    }
}
finally {
    if (Test-Path -LiteralPath $SmokeRoot) {
        Remove-Item -LiteralPath $SmokeRoot -Recurse -Force
    }
}

$HashStream = [System.IO.File]::OpenRead($ExpectedExecutable)
try {
    $HashBytes = [System.Security.Cryptography.SHA256]::Create().ComputeHash($HashStream)
    $Hash = ([System.BitConverter]::ToString($HashBytes) -replace "-", "").ToLowerInvariant()
}
finally {
    $HashStream.Dispose()
}
Set-Content -LiteralPath "$ExpectedExecutable.sha256" -Encoding ascii -NoNewline -Value "$Hash  codentum-engine.exe"
Write-Host "Engine onedir build verified: $ExpectedExecutable"
Write-Host "SHA-256: $Hash"
