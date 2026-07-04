param(
    [string]$EnvFile = "D:\cursor-hackaton\temp\genko_mcp.env",
    [string]$BridgeScript = "D:\cursor-hackaton\backend\scripts\genko_mcp_stdio.py"
)

if (-not (Test-Path -LiteralPath $EnvFile)) {
    throw "Missing env file: $EnvFile"
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
