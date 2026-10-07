$telokRoot = Split-Path -Parent $PSScriptRoot
foreach ($telokName in @('api','worker','bot')) {
    $telokPidFile = Join-Path $telokRoot "logs/$telokName.pid"
    $telokAlive = $false
    if (Test-Path -LiteralPath $telokPidFile) { $telokAlive = [bool](Get-Process -Id ([int](Get-Content -LiteralPath $telokPidFile)) -ErrorAction SilentlyContinue) }
    Write-Host "$telokName : $telokAlive"
}
try { Invoke-RestMethod 'http://127.0.0.1:8481/api/health' } catch { Write-Host 'API unavailable' }
