$ErrorActionPreference = "Stop"
$telokRoot = Split-Path -Parent $PSScriptRoot
$telokExpected = Join-Path $telokRoot '.venv\Scripts\python.exe'
foreach ($telokName in @('api','worker','bot')) {
    $telokPidFile = Join-Path $telokRoot "logs/$telokName.pid"
    if (!(Test-Path -LiteralPath $telokPidFile)) { continue }
    $telokProcessId = [int](Get-Content -LiteralPath $telokPidFile)
    $telokProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$telokProcessId"
    if (!$telokProcess) { continue }
    if ($telokProcess.ExecutablePath -ne $telokExpected) { throw "PID identity mismatch: $telokName" }
    $telokChildren = Get-CimInstance Win32_Process -Filter "ParentProcessId=$telokProcessId"
    foreach ($telokChild in $telokChildren) { Stop-Process -Id $telokChild.ProcessId -ErrorAction SilentlyContinue }
    Stop-Process -Id $telokProcessId -ErrorAction SilentlyContinue
}
Write-Host 'Telok processes stopped. Database and data retained. A stopped process does not prove Telegram did not receive a message.'
