param([switch]$Bot, [switch]$LocalStudio)
$ErrorActionPreference = "Stop"
$telokRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $telokRoot
$telokPython = Join-Path $telokRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $telokPython)) { throw 'Install first: py -3.12 -m venv .venv; .venv\Scripts\python -m pip install -r requirements.txt' }
if (!(Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
docker info --format '{{.ServerVersion}}' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop, then retry.' }
$telokContainer = docker inspect telok-postgres 2>$null | ConvertFrom-Json
if ($telokContainer) {
    if ($telokContainer.Mounts.Name -notcontains 'telok_pgdata') { throw 'Existing telok-postgres is not our development database.' }
    if (!$telokContainer.State.Running) { docker start telok-postgres | Out-Null }
} else {
    docker volume create telok_pgdata | Out-Null
    docker compose -f compose.dev.yml up -d
}
if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL startup failed.' }
$telokReady = $false
for ($telokTry = 0; $telokTry -lt 30; $telokTry++) {
    docker exec telok-postgres pg_isready -U telok -d telok *> $null
    if ($LASTEXITCODE -eq 0) { $telokReady = $true; break }
    Start-Sleep -Seconds 1
}
if (!$telokReady) { throw 'PostgreSQL did not become ready.' }
& $telokPython -m alembic upgrade head
if ($LASTEXITCODE -ne 0) { throw 'Migrations failed; worker was not started.' }
New-Item -ItemType Directory -Path logs -Force | Out-Null
$telokProcesses = @(@('worker','-m telok.worker'),@('api','-m uvicorn telok.api:app --host 127.0.0.1 --port 8481 --no-access-log'))
if ($Bot) { $telokProcesses += ,@('bot','-m telok.telegram') }
foreach ($telokEntry in $telokProcesses) {
    $telokName, $telokArguments = $telokEntry
    $telokPidFile = Join-Path $telokRoot "logs/$telokName.pid"
    if (Test-Path -LiteralPath $telokPidFile) {
        $telokOld = Get-Process -Id ([int](Get-Content -LiteralPath $telokPidFile)) -ErrorAction SilentlyContinue
        if ($telokOld) { Write-Host "$telokName already running"; continue }
    }
    $telokProcess = Start-Process -FilePath $telokPython -ArgumentList $telokArguments -WorkingDirectory $telokRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput "logs/$telokName.out.log" -RedirectStandardError "logs/$telokName.err.log"
    Set-Content -LiteralPath $telokPidFile -Value $telokProcess.Id
    Start-Sleep -Milliseconds 1500
    $telokProcess.Refresh()
    if ($telokProcess.HasExited) { throw "$telokName exited. See logs/$telokName.err.log" }
}
Write-Host 'Telok: http://127.0.0.1:8481'

if ($LocalStudio) { & (Join-Path $PSScriptRoot "start-local-studio.ps1") }
