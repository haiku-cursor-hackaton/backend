param(
    [string]$EnvFile = "",
    [string]$BridgeScript = ""
)

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptRoot

if (-not $EnvFile) {
    $EnvFile = Join-Path (Split-Path -Parent $repoRoot) "temp\genko_mcp.env"
}
if (-not $BridgeScript) {
    $BridgeScript = Join-Path $scriptRoot "genko_mcp_stdio.py"
}

if (-not (Test-Path -LiteralPath $EnvFile)) {
    throw "Missing env file: $EnvFile (run scripts/seed_multi_merchant.py first)"
}

if (-not (Test-Path -LiteralPath $BridgeScript)) {
    throw "Missing bridge script: $BridgeScript"
}

Get-Content -LiteralPath $EnvFile | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) {
        return
    }

    $parts = $line -split "=", 2
    if ($parts.Length -ne 2) {
        return
    }

    [System.Environment]::SetEnvironmentVariable($parts[0], $parts[1])
}

& python $BridgeScript
exit $LASTEXITCODE
