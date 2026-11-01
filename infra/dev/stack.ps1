# Local stack on Windows.   .\infra\dev\stack.ps1 up | down | status | prod | prod-down | demo | demo-off
# up: containers, migrations, API, both workers and the dashboard, each detached with logs in
# <data>\logs. down: stops those processes and containers; the data volumes are kept.
# prod: the real production images on this PC, everything at http://localhost:3000, with their own
# empty database and secrets in infra\deploy\local.env. prod-down: stops them and keeps the data.
# demo: prod plus simulated drivers for a presentation, and it stays on until demo-off, which removes
# them and their stock again.
# Expects the venv at $HOME\reliefroute-venv (see README); RELIEFROUTE_VENV overrides it.
# The first `up` creates an operator token and an enrolment code in backend\.env, for scripts and the legacy
# vehicle endpoint. Web sign-up needs no codes: where approval is enforced (production), a duty officer or the
# operator token approves new duty officers and drivers.
param([ValidateSet("up", "down", "status", "prod", "prod-down", "demo", "demo-off")][string]$Action = "status")

$repo = Resolve-Path "$PSScriptRoot\..\.."
$venv = if ($env:RELIEFROUTE_VENV) { $env:RELIEFROUTE_VENV } else { "$HOME\reliefroute-venv" }
$data = if ($env:DATA_DIR) { $env:DATA_DIR } else { "$HOME\reliefroute-data" }
$logs = "$data\logs"
$pids = "$logs\stack.pids.json"
$python = "$venv\Scripts\python.exe"
$npm = (Get-Command npm.cmd).Source

function Ensure-Secret($name, $value) {
    $file = "$repo\backend\.env"
    if (Select-String -Path $file -Pattern "^$name=.+" -Quiet) { return }
    Add-Content $file "$name=$value"
    "$name created in backend\.env"
}

function New-Secret { ([guid]::NewGuid().ToString("N")) + ([guid]::NewGuid().ToString("N")) }

function Ensure-LocalProdEnv {
    $file = "$repo\infra\deploy\local.env"
    if (Test-Path $file) { return }
    $gemini = Select-String -Path "$repo\backend\.env" -Pattern "^GEMINI_API_KEY=.+" | Select-Object -First 1
    $values = @{
        PUBLIC_URL = "http://localhost:3000"; DATA_DIR = $data -replace "\\", "/"; POSTGRES_PASSWORD = New-Secret
        APP_DB_PASSWORD = New-Secret; REDIS_PASSWORD = New-Secret; OPERATOR_TOKEN = New-Secret
        ENROLMENT_CODE = (New-Secret).Substring(0, 16)
    }
    $lines = foreach ($line in Get-Content "$repo\infra\deploy\.env.example") {
        $name = ($line -split "=", 2)[0]
        if ($values.ContainsKey($name)) { "$name=$($values[$name])" }
        elseif ($name -eq "GEMINI_API_KEY" -and $gemini) { $gemini.Line }
        else { $line }
    }
    @("ENV_FILE=local.env") + $lines | Set-Content -Encoding ascii $file
    "created infra\deploy\local.env"
}

function Compose-Prod {
    $envFile = "$repo\infra\deploy\local.env"
    $demo = if (Select-String -Path $envFile -Pattern "^DEMO_FLEET=true" -Quiet) { @("--profile", "demo") } else { @() }
    docker compose -f "$repo\infra\deploy\docker-compose.prod.yml" -f "$repo\infra\deploy\docker-compose.local.yml" `
        --env-file $envFile @demo @args
}

function Set-LocalEnv($name, $value) {
    $file = "$repo\infra\deploy\local.env"
    $lines = @(Get-Content $file | Where-Object { $_ -notmatch "^$name=" }) + "$name=$value"
    $lines | Set-Content -Encoding ascii $file
}

function Start-Prod {
    New-Item -ItemType Directory -Force $logs | Out-Null
    Stop-Parts
    Push-Location $repo
    docker compose --profile routing stop 2>&1 | Out-Null
    Pop-Location
    Ensure-LocalProdEnv
    Compose-Prod up -d --build
    Compose-Prod restart caddy  # it reads its config once, and `up` does not notice a changed Caddyfile
    "ReliefRoute http://localhost:3000"
    "Sign up needs no codes. Duty officers and drivers wait for approval; OPERATOR_TOKEN (infra\deploy\local.env) approves the first duty officer"
    "A new database fills from the live sources: facilities in about 5 minutes, stock in about 7"
    if (Select-String -Path "$repo\infra\deploy\local.env" -Pattern "^DEMO_FLEET=true" -Quiet) {
        "Presentation mode: simulated drivers are on. The script is in docs\demo-script.md"
    }
}

function Start-Part($name, $exe, $arguments, $dir) {
    Start-Process $exe -ArgumentList $arguments -WorkingDirectory $dir -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput "$logs\$name.out.log" -RedirectStandardError "$logs\$name.err.log"
}

function Stop-Parts {
    if (-not (Test-Path $pids)) { return }
    foreach ($part in (Get-Content $pids -Raw | ConvertFrom-Json)) {
        # /T also ends the node process npm starts.
        & taskkill /PID $part.pid /T /F 2>&1 | Out-Null
        "stopped $($part.name)"
    }
    Remove-Item $pids
}

switch ($Action) {
    "prod" { Start-Prod }
    "demo" {
        Ensure-LocalProdEnv
        Set-LocalEnv "DEMO_FLEET" "true"
        Start-Prod
    }
    "demo-off" {
        Compose-Prod stop demo-fleet
        $token = ((Select-String -Path "$repo\infra\deploy\local.env" -Pattern "^OPERATOR_TOKEN=(.+)$").Matches[0].Groups[1].Value)
        Invoke-RestMethod -Method Delete -Uri "http://localhost:3000/api/demo" -Headers @{ "X-Operator-Token" = $token } | Out-Null
        "Simulated trucks and their stock are removed"
        Set-LocalEnv "DEMO_FLEET" "false"
        Start-Prod
    }
    "prod-down" {
        if (Test-Path "$repo\infra\deploy\local.env") { Compose-Prod stop }
    }
    "up" {
        New-Item -ItemType Directory -Force $logs | Out-Null
        Stop-Parts
        if (Test-Path "$repo\infra\deploy\local.env") { Compose-Prod stop 2>&1 | Out-Null }
        Push-Location $repo
        docker compose --profile routing up -d
        Pop-Location
        $backend = "$repo\backend"
        Ensure-Secret "OPERATOR_TOKEN" (([guid]::NewGuid().ToString("N")) + ([guid]::NewGuid().ToString("N")))
        Ensure-Secret "ENROLMENT_CODE" ([guid]::NewGuid().ToString("N").Substring(0, 16))
        Push-Location $backend
        & $python -m app.core.migrate *>&1 | Select-Object -Last 1
        Pop-Location
        $parts = @(
            @{ name = "api"; process = Start-Part "api" $python @("-m", "uvicorn", "app.main:app", "--port", "8000", "--timeout-keep-alive", "75") $backend }
            @{ name = "ingestion"; process = Start-Part "ingestion" $python @("-m", "app.ingestion.worker") $backend }
            @{ name = "brain"; process = Start-Part "brain" $python @("-m", "app.graph.worker") $backend }
            @{ name = "frontend"; process = Start-Part "frontend" $npm @("run", "dev", "--", "-H", "127.0.0.1") "$repo\frontend" }
        )
        $parts | ForEach-Object { @{ name = $_.name; pid = $_.process.Id } } | ConvertTo-Json | Set-Content $pids
        "ReliefRoute http://localhost:3000  API http://localhost:8000  logs $logs"
        "Sign up needs no codes. In this local setup (APP_ENV=development) every account is usable at once"
    }
    "down" {
        Stop-Parts
        Push-Location $repo
        docker compose --profile routing stop
        Pop-Location
    }
    "status" {
        Push-Location $repo
        docker compose --profile routing ps --format "table {{.Service}}\t{{.Status}}"
        Pop-Location
        if (Test-Path "$repo\infra\deploy\local.env") { Compose-Prod ps --format "table {{.Service}}\t{{.Status}}" }
        if (Test-Path $pids) {
            foreach ($part in (Get-Content $pids -Raw | ConvertFrom-Json)) {
                $alive = [bool](Get-Process -Id $part.pid -ErrorAction SilentlyContinue)
                "{0,-10} pid {1,-7} {2}" -f $part.name, $part.pid, $(if ($alive) { "running" } else { "STOPPED" })
            }
        }
    }
}
