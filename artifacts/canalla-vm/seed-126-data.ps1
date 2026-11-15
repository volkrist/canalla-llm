# Seed a 1.2.6 data root with the real 1.2.6 sidecar.
# Runs on the Windows host, never against production: the data dir is under D:\canalla-vm\.
$ErrorActionPreference = 'Stop'
$root = 'D:\canalla-vm'
$data = Join-Path $root 'seed126-data'
$sidecar = Join-Path $root 'payload126\sidecar\alex-backend\alex-backend.exe'
if (-not (Test-Path $sidecar)) { throw "sidecar missing: $sidecar" }
New-Item -ItemType Directory -Force -Path $data | Out-Null

$env:ALEX_LLM_DATA_DIR = $data
$env:ALEX_BACKEND_PORT = '18080'
$env:ALEX_TOR_MANAGED_ENABLED = 'false'
$process = Start-Process -FilePath $sidecar -PassThru -WindowStyle Hidden
$health = $null
for ($attempt = 0; $attempt -lt 40; $attempt++) {
  Start-Sleep -Seconds 2
  try {
    $health = Invoke-RestMethod -Uri 'http://127.0.0.1:18080/health' -TimeoutSec 3
    if ($health.status -eq 'ok') { break }
  } catch { $health = $null }
}
if ($null -eq $health) { throw 'the 1.2.6 sidecar did not answer /health' }
Write-Output ("HEALTH " + $health.product + " " + $health.version)
Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3
$db = Join-Path $data 'data\alex.db'
if (-not (Test-Path $db)) { throw "no database at $db" }
Write-Output ("DB " + (Get-Item $db).Length)
Write-Output ("RUNTIME_FILES " + ((Get-ChildItem (Join-Path $data 'runtime') -File -ErrorAction SilentlyContinue | Measure-Object).Count))
