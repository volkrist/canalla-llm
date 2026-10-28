# Make sure the installed Canalla window is running with the CDP port the acceptance driver uses.
#
# `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS` is only read at launch, so a window that is already
# running without it is restarted once. Nothing else is touched: the data root, the credentials
# and the pairing stay exactly as they are.
param([switch]$Stop)

$install = Join-Path $env:LOCALAPPDATA 'Programs\Canalla LLM'
$exe = Join-Path $install 'alex-llm.exe'

function Test-Cdp {
  try {
    $response = Invoke-WebRequest -Uri 'http://127.0.0.1:9242/json/version' -UseBasicParsing -TimeoutSec 2
    return $response.StatusCode -eq 200
  } catch {
    return $false
  }
}

function Stop-App {
  Get-Process -Name 'alex-llm' -ErrorAction SilentlyContinue | ForEach-Object { $_.CloseMainWindow() | Out-Null }
  Start-Sleep -Seconds 3
  Get-Process -Name 'alex-llm' -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.Id -Force }
  for ($i = 0; $i -lt 30; $i++) {
    if (-not (Get-Process -Name 'alex-backend' -ErrorAction SilentlyContinue)) { break }
    Start-Sleep -Milliseconds 500
  }
}

if ($Stop) {
  Stop-App
  Write-Output 'app=stopped'
  exit 0
}

if (Test-Cdp) {
  Write-Output 'cdp=ready'
  exit 0
}

if (Get-Process -Name 'alex-llm' -ErrorAction SilentlyContinue) {
  Stop-App
}

$env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = '--remote-debugging-port=9242'
Start-Process -FilePath $exe
for ($i = 0; $i -lt 120; $i++) {
  if (Test-Cdp) {
    Write-Output 'cdp=ready'
    exit 0
  }
  Start-Sleep -Seconds 1
}
Write-Output 'cdp=timeout'
exit 1
