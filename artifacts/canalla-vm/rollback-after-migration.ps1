# Rollback the way a user would do it: restore the previous binaries after 1.2.7 migrated the DB.
# The migrated data root (alembic 0017) is copied so the evidence stays intact.
$ErrorActionPreference = 'Stop'
$root = 'D:\canalla-vm\host-accept'
$evidence = Join-Path $root 'evidence'
$prodInstall = Join-Path $env:LOCALAPPDATA 'Programs\Canalla LLM'
function Say($text) { Write-Output $text; Add-Content -Path (Join-Path $evidence 'rollback.log') -Value $text }

Copy-Item (Join-Path $root 'data127') (Join-Path $root 'data-0017-rollback') -Recurse -Force
$dataDir = Join-Path $root 'data-0017-rollback'
$exe = Join-Path $prodInstall 'sidecar\alex-backend\alex-backend.exe'
Say "SIDECAR_EXE $exe"
$env:ALEX_LLM_DATA_DIR = $dataDir
$env:ALEX_BACKEND_PORT = '18093'
$env:ALEX_TOR_MANAGED_ENABLED = 'false'
$p = Start-Process -FilePath $exe -PassThru -WindowStyle Hidden
$health = $null
for ($i = 0; $i -lt 30; $i++) {
  Start-Sleep -Seconds 2
  try {
    $health = Invoke-RestMethod -Uri 'http://127.0.0.1:18093/health' -TimeoutSec 3
    if ($health.status -eq 'ok') { break }
  } catch { $health = $null }
}
if ($null -eq $health) { Say 'HEALTH FAILED' } else { Say "HEALTH version=$($health.version)" }
Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3
Copy-Item (Join-Path $dataDir 'data\alex.db') (Join-Path $evidence 'db-126-after-0017.db') -Force
Say 'DONE'
