param(
    [string]$BackendUrl = "http://127.0.0.1:8000",
    [int]$MerchantPort = 8100,
    [string]$CredentialsPath = "D:\cursor-hackaton\temp\demo_seed_credentials.json",
    [bool]$RestartBackend = $true
)

$ErrorActionPreference = "Stop"

$backendRoot = "D:\cursor-hackaton\backend"
$sdkRoot = "D:\cursor-hackaton\python-sdk"
$merchantUrl = "http://127.0.0.1:$MerchantPort"
$backendPort = [int]([Uri]$BackendUrl).Port

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

function Stop-Backend {
    param([int]$Port)

    $pattern = "*uvicorn*app.main:app*--port $Port*"
    $processes = Get-CimInstance Win32_Process |
        Where-Object { $_.CommandLine -and $_.CommandLine -like $pattern } |
        Select-Object -ExpandProperty ProcessId -Unique

    $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
    $netstatListeners = netstat -ano |
        Select-String -Pattern "127\.0\.0\.1:$Port\s+.*LISTENING\s+(\d+)$" |
        ForEach-Object { $_.Matches[0].Groups[1].Value }

    $targets = @($processes) + @($listeners) + @($netstatListeners) |
        Where-Object { $_ } |
        Sort-Object -Descending -Unique

    foreach ($targetPid in $targets) {
        try {
            Stop-Process -Id $targetPid -Force -ErrorAction Stop
        } catch {
        }
    }
}

function Start-Backend {
    param([int]$Port)

    $command = @(
        "Set-Location '$backendRoot'"
        "python -m uvicorn app.main:app --port $Port"
    ) -join "; "

    Start-Process powershell -WindowStyle Hidden -ArgumentList @(
        "-NoProfile",
        "-Command",
        $command
    ) | Out-Null
}

function Start-DemoStore {
    param(
        [string]$PlatformUrl,
        [int]$Port,
        [string]$PlatformApiKey = ""
    )

    $command = @(
        "`$env:UCP_PLATFORM_URL='$PlatformUrl'"
        "if ('$PlatformApiKey') { `$env:UCP_PLATFORM_API_KEY='$PlatformApiKey' } else { Remove-Item Env:UCP_PLATFORM_API_KEY -ErrorAction SilentlyContinue }"
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

if ($RestartBackend) {
    Write-Host "0/5 Reiniciando backend en $BackendUrl"
    Stop-Backend -Port $backendPort
    Start-Backend -Port $backendPort
}

try {
    Wait-HttpOk -Url "$BackendUrl/health" -TimeoutSeconds 20
} catch {
    throw "Backend no disponible en $BackendUrl. Levantalo antes de correr este script."
}

Write-Host "1/5 Reiniciando demo store bootstrap en $merchantUrl"
Stop-DemoStore -Port $MerchantPort
Start-DemoStore -PlatformUrl $BackendUrl -Port $MerchantPort
Wait-HttpOk -Url "$merchantUrl/.well-known/ucp" -TimeoutSeconds 30

Write-Host "2/5 Ejecutando reseed del demo contra $merchantUrl"
Set-Location $backendRoot
python scripts/seed_demo.py --apply --backend-url $BackendUrl --merchant-url $merchantUrl --output $CredentialsPath
if ($LASTEXITCODE -ne 0) {
    throw "seed_demo.py fallo."
}

if (-not (Test-Path $CredentialsPath)) {
    throw "No se genero el archivo de credenciales: $CredentialsPath"
}

$creds = Get-Content -Path $CredentialsPath -Raw | ConvertFrom-Json
if (-not $creds.sdk_api_key) {
    throw "El seed no devolvio sdk_api_key."
}

Write-Host "3/5 Reiniciando demo store con sdk_api_key fresca"
Stop-DemoStore -Port $MerchantPort
Start-DemoStore -PlatformUrl $BackendUrl -Port $MerchantPort -PlatformApiKey $creds.sdk_api_key
Wait-HttpOk -Url "$merchantUrl/.well-known/ucp" -TimeoutSeconds 30

Write-Host "4/5 Ejecutando smoke E2E"
python scripts/smoke_test.py --credentials $CredentialsPath
$smokeExitCode = $LASTEXITCODE

Write-Host "5/5 Validacion finalizada"
exit $smokeExitCode
