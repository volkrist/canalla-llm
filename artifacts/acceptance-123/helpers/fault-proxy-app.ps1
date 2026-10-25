# Launch the installed app with the test-only fault proxy in front of the Gateway.
#
# H6 must survive a real backend -> Gateway interruption. The production Gateway is never touched:
# the proxy forwards normal traffic byte-for-byte and can refuse the tunnel for a few seconds.
#
#   -Fault     start the proxy, launch the app with HTTPS_PROXY, wait for /health
#   -Restore   stop the proxy, launch the app again without the proxy
param(
  [switch]$Fault,
  [switch]$Restore
)
$ErrorActionPreference = 'Stop'
$repo = 'C:\Users\Volkr\Documents\Codex\2026-09-13\x20\outputs\worktrees\alex-llm\ruddy-jackal\alex-llm'
$python = Join-Path $repo 'apps\backend\.venv\Scripts\python.exe'
$proxyScript = Join-Path $repo 'artifacts\acceptance-123\fault_proxy.py'
$app = Join-Path $env:LOCALAPPDATA 'Programs\Canalla LLM\alex-llm.exe'
$pidFile = Join-Path $repo 'artifacts\fault-proxy.pid'

if (-not $Fault -and -not $Restore) { throw 'USE -Fault or -Restore' }

function Stop-Canalla {
  Get-Process -Name 'alex-llm' -ErrorAction SilentlyContinue | ForEach-Object { $_.CloseMainWindow() | Out-Null }
  Start-Sleep -Seconds 6
  Get-Process -Name 'alex-llm' -ErrorAction SilentlyContinue | ForEach-Object { $_.WaitForExit(15000) | Out-Null }
  $productRoot = Join-Path $env:LOCALAPPDATA 'Programs\Canalla LLM'
  foreach ($sidecar in @(Get-Process -Name 'alex-backend' -ErrorAction SilentlyContinue)) {
    if ($sidecar -and $sidecar.Path -and $sidecar.Path.StartsWith($productRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
      Stop-Process -Id $sidecar.Id -Force
    }
  }
  Start-Sleep -Seconds 2
}

function Wait-Health {
  for ($i = 0; $i -lt 60; $i++) {
    try {
      $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/health' -UseBasicParsing -TimeoutSec 5
      return $r.Content
    } catch { Start-Sleep -Seconds 1 }
  }
  return 'health unavailable'
}

Stop-Canalla

if ($Restore) {
  if (Test-Path $pidFile) {
    $old = Get-Content $pidFile | Select-Object -First 1
    $process = Get-Process -Id ([int]$old) -ErrorAction SilentlyContinue
    if ($process) { Stop-Process -Id $process.Id -Force }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
  }
  Remove-Item Env:HTTPS_PROXY -ErrorAction SilentlyContinue
  Remove-Item Env:HTTP_PROXY -ErrorAction SilentlyContinue
  Remove-Item Env:NO_PROXY -ErrorAction SilentlyContinue
  Start-Process -FilePath $app
  Start-Sleep -Seconds 2
  Write-Output ('restored health ' + (Wait-Health))
  exit 0
}

$proxy = Start-Process -FilePath $python -ArgumentList @('"' + $proxyScript + '"', '--port', '8765', '--control-port', '8766') -PassThru -WindowStyle Hidden
Set-Content -Path $pidFile -Value $proxy.Id
Start-Sleep -Seconds 2
try {
  $status = Invoke-WebRequest -Uri 'http://127.0.0.1:8766/status' -Method Post -UseBasicParsing -TimeoutSec 5
  Write-Output ('proxy ' + $proxy.Id + ' ' + $status.Content)
} catch {
  throw ('FAULT_PROXY_NOT_READY: ' + $_.Exception.Message)
}

$env:HTTPS_PROXY = 'http://127.0.0.1:8765'
$env:HTTP_PROXY = 'http://127.0.0.1:8765'
$env:NO_PROXY = '127.0.0.1,localhost'
Start-Process -FilePath $app
Start-Sleep -Seconds 2
Write-Output ('fault health ' + (Wait-Health))
