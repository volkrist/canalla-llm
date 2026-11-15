# Data compatibility across the real upgrade and rollback.
# The 1.2.7 payload was preserved before production was restored; the 1.2.6 payload is the
# user's install. Both are started against a copy of a data root created by the 1.2.6 sidecar.
$ErrorActionPreference = 'Stop'
$root = 'D:\canalla-vm\host-accept'
$evidence = Join-Path $root 'evidence'
$seed = Join-Path $root 'seed126-data'
$payload127 = Join-Path $root 'payload127'
$prodInstall = Join-Path $env:LOCALAPPDATA 'Programs\Canalla LLM'
New-Item -ItemType Directory -Force -Path $evidence | Out-Null

function Say($text) { Write-Output $text; Add-Content -Path (Join-Path $evidence 'data.log') -Value $text }

function Health($label, $port) {
  for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 2
    try {
      $r = Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 3
      if ($r.status -eq 'ok') { Say "HEALTH $label version=$($r.version)"; return $r }
    } catch { }
  }
  Say "HEALTH $label FAILED"
  return $null
}

function Run($label, $install, $dataDir, $port) {
  $exe = Join-Path $install 'sidecar\alex-backend\alex-backend.exe'
  if (-not (Test-Path $exe)) { Say "SIDECAR $label missing"; return }
  Say "SIDECAR $label $((Get-Item $exe).VersionInfo.ProductVersion)"
  $env:ALEX_LLM_DATA_DIR = $dataDir
  $env:ALEX_BACKEND_PORT = "$port"
  $env:ALEX_TOR_MANAGED_ENABLED = 'false'
  $p = Start-Process -FilePath $exe -PassThru -WindowStyle Hidden
  $null = Health $label $port
  Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 3
  $db = Join-Path $dataDir 'data\alex.db'
  if (Test-Path $db) { Copy-Item $db (Join-Path $evidence "db-$label.db") -Force }
  $migration = Join-Path $dataDir 'runtime\migration.json'
  if (Test-Path $migration) { Copy-Item $migration (Join-Path $evidence "migration-$label.json") -Force }
}

# 1.2.7 binaries against data created by 1.2.6
Copy-Item $seed (Join-Path $root 'data127') -Recurse -Force
Run '127' $payload127 (Join-Path $root 'data127') 18091

# 1.2.6 binaries (the restored user install) against the same data
Copy-Item $seed (Join-Path $root 'data126') -Recurse -Force
Run '126-rollback' $prodInstall (Join-Path $root 'data126') 18092

Say 'DONE'
