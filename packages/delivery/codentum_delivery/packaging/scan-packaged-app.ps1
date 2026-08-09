[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$AppDirectory,

    [Parameter()]
    [string]$NodeExecutable = "node",

    [Parameter()]
    [string]$PythonExecutable = "python"
)

$ErrorActionPreference = "Stop"
$PackagingRoot = $PSScriptRoot
$DeliveryRoot = (Resolve-Path (Join-Path $PackagingRoot "..\..")).Path
$DesktopRoot = (Resolve-Path (Join-Path $DeliveryRoot "..\desktop")).Path
$ResolvedApp = (Resolve-Path -LiteralPath $AppDirectory).Path
$AsarPath = Join-Path $ResolvedApp "resources\app.asar"
$ResourcesPath = Join-Path $ResolvedApp "resources"
$AsarCli = Join-Path $DesktopRoot "node_modules\@electron\asar\bin\asar.js"
foreach ($Required in @($AsarPath, $ResourcesPath, $AsarCli)) {
    if (-not (Test-Path -LiteralPath $Required)) {
        throw "Packaged-app scan input is missing: $Required"
    }
}

$TempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$ExtractRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $TempRoot ("codentum-asar-scan-" + [guid]::NewGuid().ToString("N")))
)
if (-not $ExtractRoot.StartsWith($TempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to extract outside the system temporary directory."
}

$PreviousPythonPath = $env:PYTHONPATH
try {
    New-Item -ItemType Directory -Path $ExtractRoot | Out-Null
    & $NodeExecutable $AsarCli extract $AsarPath $ExtractRoot
    if ($LASTEXITCODE -ne 0) { throw "app.asar extraction failed with exit code $LASTEXITCODE." }
    $env:PYTHONPATH = $DeliveryRoot
    & $PythonExecutable -m codentum_delivery.secret_scan.bundle --root $ExtractRoot --label "app.asar"
    if ($LASTEXITCODE -ne 0) { throw "Extracted app.asar secret scan failed." }
    & $PythonExecutable -m codentum_delivery.secret_scan.bundle --root $ResourcesPath --label "extraResources"
    if ($LASTEXITCODE -ne 0) { throw "Packaged resource secret scan failed." }
}
finally {
    $env:PYTHONPATH = $PreviousPythonPath
    if (Test-Path -LiteralPath $ExtractRoot) {
        $Verified = [System.IO.Path]::GetFullPath($ExtractRoot)
        if (-not $Verified.StartsWith($TempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to clean a directory outside the system temporary directory."
        }
        Remove-Item -LiteralPath $Verified -Recurse -Force
    }
}
