[CmdletBinding()]
param(
    [Parameter()]
    [string]$PythonExecutable = "python"
)

$ErrorActionPreference = "Stop"
$PackagingRoot = $PSScriptRoot
$RepositoryRoot = (Resolve-Path (Join-Path $PackagingRoot "..\..\..\..")).Path
$RequirementsFile = Join-Path $RepositoryRoot "packages\delivery\requirements-build.txt"
$BuildEnvironment = Join-Path ([System.IO.Path]::GetTempPath()) ("codentum-runtime-build-" + [guid]::NewGuid().ToString("N"))

try {
    & $PythonExecutable -m venv $BuildEnvironment
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create the isolated Python packaging environment."
    }
    $BuildPython = Join-Path $BuildEnvironment "Scripts\python.exe"
    & $BuildPython -m pip install --disable-pip-version-check -r $RequirementsFile $RepositoryRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Could not install the locked build dependency and project runtime dependencies."
    }

    & (Join-Path $PackagingRoot "build-engine.ps1") -PythonExecutable $BuildPython
    if ($LASTEXITCODE -ne 0) {
        throw "Engine packaging failed."
    }
    & (Join-Path $PackagingRoot "build-sidecar.ps1") -PythonExecutable $BuildPython
    if ($LASTEXITCODE -ne 0) {
        throw "Sidecar packaging failed."
    }
}
finally {
    if (Test-Path -LiteralPath $BuildEnvironment) {
        Remove-Item -LiteralPath $BuildEnvironment -Recurse -Force
    }
}
