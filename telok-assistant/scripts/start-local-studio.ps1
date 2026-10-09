param([string]$ComfyRoot = $env:TELOK_COMFY_ROOT)
$ErrorActionPreference = "Stop"
$telokRoot = Split-Path -Parent $PSScriptRoot
$telokPathFile = Join-Path $telokRoot 'data/local-studio-path.txt'
if (!$ComfyRoot -and (Test-Path -LiteralPath $telokPathFile)) { $ComfyRoot = (Get-Content -LiteralPath $telokPathFile -Raw).Trim() }
if (!$ComfyRoot) { throw 'Set TELOK_COMFY_ROOT or data/local-studio-path.txt to the existing ComfyUI portable folder.' }
$telokComfyRoot = $ComfyRoot
try {
    $telokStatus = Invoke-RestMethod 'http://127.0.0.1:8188/system_stats' -TimeoutSec 3
    if ($telokStatus.system) { Write-Host 'ComfyUI already running'; exit 0 }
} catch {}
$telokComfyPython = Join-Path $telokComfyRoot 'python_embeded\python.exe'
if (!(Test-Path -LiteralPath $telokComfyPython)) { throw 'ComfyUI portable not found. Update scripts/start-local-studio.ps1 for this computer.' }
New-Item -ItemType Directory -Path (Join-Path $telokRoot 'logs') -Force | Out-Null
$telokProcess = Start-Process -FilePath $telokComfyPython -ArgumentList '-s ComfyUI\main.py --windows-standalone-build --listen 127.0.0.1 --port 8188 --disable-auto-launch --disable-all-custom-nodes' -WorkingDirectory $telokComfyRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $telokRoot 'logs/comfy.out.log') -RedirectStandardError (Join-Path $telokRoot 'logs/comfy.err.log')
Set-Content -LiteralPath (Join-Path $telokRoot 'logs/comfy.pid') -Value $telokProcess.Id
Write-Host 'Local studio starting: http://127.0.0.1:8188'
