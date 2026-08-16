[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SidecarPath,

    [Parameter()]
    [ValidateRange(1, 120)]
    [int]$TimeoutSeconds = 10,

    [Parameter()]
    [string]$ProjectRoot = ""
)

$ErrorActionPreference = "Stop"
$ExpectedCapabilities = @(
    "requirements",
    "planConfirmation",
    "pauseAtSafePoint",
    "resume",
    "stop",
    "keepMemory",
    "forkFromCheckpoint",
    "appendPrompt",
    "insertModule"
)

$ResolvedSidecar = (Resolve-Path -LiteralPath $SidecarPath).Path
if (-not (Test-Path -LiteralPath $ResolvedSidecar -PathType Leaf)) {
    throw "Sidecar executable is missing: $SidecarPath"
}

$StartInfo = [System.Diagnostics.ProcessStartInfo]::new()
$StartInfo.FileName = $ResolvedSidecar
$StartInfo.UseShellExecute = $false
$StartInfo.CreateNoWindow = $true
$StartInfo.RedirectStandardInput = $true
$StartInfo.RedirectStandardOutput = $true
$StartInfo.RedirectStandardError = $true
$StartInfo.StandardOutputEncoding = [System.Text.UTF8Encoding]::new($false)
$StartInfo.StandardErrorEncoding = [System.Text.UTF8Encoding]::new($false)

$Process = [System.Diagnostics.Process]::new()
$Process.StartInfo = $StartInfo
if (-not $Process.Start()) {
    throw "Sidecar process could not be started."
}
$StderrTask = $Process.StandardError.ReadToEndAsync()

try {
    $HandshakeRequest = @{ id = "cold-start-handshake"; method = "handshake"; params = @{ protocolVersion = 1 } } |
        ConvertTo-Json -Compress -Depth 5
    $Process.StandardInput.WriteLine($HandshakeRequest)
    $Process.StandardInput.Flush()
    $HandshakeTask = $Process.StandardOutput.ReadLineAsync()
    if (-not $HandshakeTask.Wait($TimeoutSeconds * 1000)) {
        throw "Sidecar handshake timed out after $TimeoutSeconds seconds."
    }
    $Envelope = $HandshakeTask.Result | ConvertFrom-Json
    if ($Envelope.id -ne "cold-start-handshake" -or $Envelope.ok -ne $true) {
        throw "Sidecar returned a failed or mismatched handshake envelope."
    }
    $Handshake = $Envelope.result
    if ($Handshake.protocolVersion -ne 1) {
        throw "Sidecar protocol mismatch: expected 1, received $($Handshake.protocolVersion)."
    }
    foreach ($Name in $ExpectedCapabilities) {
        $Property = $Handshake.capabilities.PSObject.Properties[$Name]
        if ($null -eq $Property -or $Property.Value -isnot [bool]) {
            throw "Sidecar handshake is missing boolean capability '$Name'."
        }
    }
    if ($Handshake.connected -ne $true) {
        $Reason = if ($Handshake.unavailableReason) { $Handshake.unavailableReason } else { "no reason supplied" }
        throw "Real A/B engine is unavailable: $Reason"
    }

    if (-not [string]::IsNullOrWhiteSpace($ProjectRoot)) {
        $ResolvedProject = (Resolve-Path -LiteralPath $ProjectRoot).Path
        $CommandId = "cold-start-requirement"
        $RequestedAt = (Get-Date).ToUniversalTime().ToString('o')
        $CommandPayload = @{
            id = "cold-start-command"
            method = "command"
            params = @{
                command = @{
                    commandId = $CommandId
                    runId = $Handshake.runId
                    expectedRevision = $Handshake.stateRevision
                    target = @{ agentId = "operator" }
                    action = "submit_requirement"
                    payload = @{
                        projectRoot = $ResolvedProject
                        requirement = "Verify that the packaged A/B engine creates a real task."
                    }
                    requestedAt = $RequestedAt
                }
            }
        }
        $CommandRequest = $CommandPayload | ConvertTo-Json -Compress -Depth 8
        $Process.StandardInput.WriteLine($CommandRequest)
        $Process.StandardInput.Flush()
        $CommandTask = $Process.StandardOutput.ReadLineAsync()
        if (-not $CommandTask.Wait($TimeoutSeconds * 1000)) {
            throw "Real A/B engine requirement submission timed out."
        }
        $CommandEnvelope = $CommandTask.Result | ConvertFrom-Json
        if ($CommandEnvelope.id -ne "cold-start-command" -or $CommandEnvelope.ok -ne $true) {
            throw "Real A/B engine returned a failed requirement envelope."
        }
        if ($CommandEnvelope.result.commandId -ne $CommandId -or $CommandEnvelope.result.status -ne "accepted") {
            throw "Real A/B engine did not accept the cold-start requirement: status=$($CommandEnvelope.result.status), reason=$($CommandEnvelope.result.reason)"
        }
        $Packets = @(Get-ChildItem -LiteralPath (Join-Path $ResolvedProject ".codentum\packets") -File -Filter "*.json" -ErrorAction SilentlyContinue)
        if ($Packets.Count -lt 1) {
            throw "Real A/B engine accepted the requirement but did not create a WorkPacket."
        }
    }

    $ShutdownRequest = @{ id = "cold-start-shutdown"; method = "shutdown"; params = @{} } |
        ConvertTo-Json -Compress -Depth 3
    $Process.StandardInput.WriteLine($ShutdownRequest)
    $Process.StandardInput.Flush()
    $ShutdownTask = $Process.StandardOutput.ReadLineAsync()
    if (-not $ShutdownTask.Wait($TimeoutSeconds * 1000)) {
        throw "Sidecar shutdown acknowledgement timed out."
    }
    $ShutdownEnvelope = $ShutdownTask.Result | ConvertFrom-Json
    if ($ShutdownEnvelope.id -ne "cold-start-shutdown" -or $ShutdownEnvelope.ok -ne $true) {
        throw "Sidecar did not acknowledge graceful shutdown."
    }
    if (-not $Process.WaitForExit($TimeoutSeconds * 1000) -or $Process.ExitCode -ne 0) {
        throw "Sidecar did not exit cleanly after shutdown."
    }
    if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
        Write-Host "PASS: sidecar protocol, real-engine handshake, and graceful shutdown verified."
    }
    else {
        Write-Host "PASS: sidecar protocol, real-engine handshake, requirement submission, WorkPacket creation, and graceful shutdown verified."
    }
}
finally {
    if (-not $Process.HasExited) {
        $Process.Kill()
        $Process.WaitForExit()
    }
    $null = $StderrTask.Result
    $Process.Dispose()
}
