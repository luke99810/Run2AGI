[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InstallerPath,

    [Parameter()]
    [string]$SignToolPath = ""
)

$ErrorActionPreference = "Stop"
$ResolvedInstaller = (Resolve-Path -LiteralPath $InstallerPath).Path
$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..\..")).Path
if ([string]::IsNullOrWhiteSpace($SignToolPath)) {
    $Command = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($null -ne $Command) {
        $SignToolPath = $Command.Source
    }
    else {
        $SignToolPath = Join-Path $RepositoryRoot "packages\desktop\node_modules\@electron\windows-sign\vendor\signtool.exe"
    }
}
if (-not (Test-Path -LiteralPath $SignToolPath -PathType Leaf)) {
    throw "signtool.exe is unavailable; install desktop dependencies or provide -SignToolPath."
}

& $SignToolPath verify /pa /all $ResolvedInstaller
if ($LASTEXITCODE -ne 0) {
    throw "Windows installer does not have a valid trusted Authenticode signature."
}
Write-Host "PASS: Authenticode signature and trust chain are valid."
