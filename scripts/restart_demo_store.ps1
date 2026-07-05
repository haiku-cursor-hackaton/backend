param(
    [string]$BackendUrl = "http://127.0.0.1:8002",
    [int]$MerchantPort = 8113,
    [string]$CredentialsPath = "D:\cursor-hackaton\temp\demo_seed_credentials.json"
)

$ErrorActionPreference = "Stop"

$sdkRoot = "D:\cursor-hackaton\python-sdk"
$merchantUrl = "http://127.0.0.1:$MerchantPort"

function Wait-HttpOk {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$TimeoutSeconds = 30
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
                return
            }
        } catch {
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)

    throw "Timed out waiting for $Url"
}

function Stop-DemoStore {
    param([int]$Port)

    $pattern = "*uvicorn*examples.demo_store:app*--port $Port*"
    $targets = Get-CimInstance Win32_Process |
        Where-Object { $_.CommandLine -and $_.CommandLine -like $pattern } |
        Select-Object -ExpandProperty ProcessId

    $netstatTargets = netstat -ano |
        Select-String -Pattern "127\.0\.0\.1:$Port\s+.*LISTENING\s+(\d+)$" |
        ForEach-Object { $_.Matches[0].Groups[1].Value }

    foreach ($targetPid in ((@($targets) + @($netstatTargets)) | Sort-Object -Descending -Unique)) {
        try {
            Stop-Process -Id $targetPid -Force -ErrorAction Stop
        } catch {
        }
    }
}

function Start-DemoStore {
    param(
        [string]$PlatformUrl,
        [int]$Port,
        [string]$PlatformApiKey
    )

    $command = @(
        "`$env:UCP_PLATFORM_URL='$PlatformUrl'"
        "`$env:UCP_PLATFORM_API_KEY='$PlatformApiKey'"
        "`$env:UCP_DEMO_ORDER_CAPABILITY='1'"
        "`$env:UCP_DEMO_BASE_URL='http://127.0.0.1:$Port'"
        "Set-Location '$sdkRoot'"
        "python -m uvicorn examples.demo_store:app --port $Port"
    ) -join "; "

    Start-Process powershell -WindowStyle Hidden -ArgumentList @(
        "-NoProfile",
        "-Command",
        $command
    ) | Out-Null
}

if (-not (Test-Path $CredentialsPath)) {
    throw "Credentials file not found: $CredentialsPath"
}

$creds = Get-Content -Path $CredentialsPath -Raw | ConvertFrom-Json
if (-not $creds.sdk_api_key) {
    throw "Credentials file missing sdk_api_key: $CredentialsPath"
}

Write-Host "Stopping demo store on port $MerchantPort"
Stop-DemoStore -Port $MerchantPort

Write-Host "Starting demo store with sdk_api_key prefix: $($creds.sdk_api_key_prefix)"
Start-DemoStore -PlatformUrl $BackendUrl -Port $MerchantPort -PlatformApiKey $creds.sdk_api_key
Wait-HttpOk -Url "$merchantUrl/.well-known/ucp" -TimeoutSeconds 30

Write-Host "Demo store ready at $merchantUrl"
Write-Host "Platform URL: $BackendUrl"
Write-Host "SDK key prefix: $($creds.sdk_api_key_prefix)"
