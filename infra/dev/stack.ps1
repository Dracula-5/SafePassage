# Local stack on Windows.   .\infra\dev\stack.ps1 up | down | status
# up: containers, migrations, API, both workers and the dashboard, each detached with logs in
# <data>\logs. down: stops those processes and containers; the data volumes are kept.
# Expects the venv at $HOME\reliefroute-venv (see README); RELIEFROUTE_VENV overrides it.
# The first `up` creates an operator token and a driver enrolment code in backend\.env, so an open
# tunnel can never mean an open API.
param([ValidateSet("up", "down", "status")][string]$Action = "status")

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
    "up" {
        New-Item -ItemType Directory -Force $logs | Out-Null
        Stop-Parts
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
            @{ name = "api"; process = Start-Part "api" $python @("-m", "uvicorn", "app.main:app", "--port", "8000") $backend }
            @{ name = "ingestion"; process = Start-Part "ingestion" $python @("-m", "app.ingestion.worker") $backend }
            @{ name = "brain"; process = Start-Part "brain" $python @("-m", "app.graph.worker") $backend }
            @{ name = "frontend"; process = Start-Part "frontend" $npm @("run", "dev", "--", "-H", "127.0.0.1") "$repo\frontend" }
        )
        $parts | ForEach-Object { @{ name = $_.name; pid = $_.process.Id } } | ConvertTo-Json | Set-Content $pids
        "API http://localhost:8000  dashboard http://localhost:3000  driver http://localhost:8000/driver/  logs $logs"
        "The dashboard asks for OPERATOR_TOKEN and the driver app for ENROLMENT_CODE; both are in backend\.env"
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
        if (Test-Path $pids) {
            foreach ($part in (Get-Content $pids -Raw | ConvertFrom-Json)) {
                $alive = [bool](Get-Process -Id $part.pid -ErrorAction SilentlyContinue)
                "{0,-10} pid {1,-7} {2}" -f $part.name, $part.pid, $(if ($alive) { "running" } else { "STOPPED" })
            }
        }
    }
}
