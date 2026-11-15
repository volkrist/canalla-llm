# In-guest acceptance: clean install, upgrade over 1.2.6 data, rollback, uninstall.
# Runs inside the disposable Windows 11 VM. Never touches the host or production.
$ErrorActionPreference = 'Stop'
$share = '\\VBOXSVR\payload'
$install = Join-Path $env:LOCALAPPDATA 'Programs\Canalla LLM'
$dataRoot = Join-Path $env:LOCALAPPDATA 'Alex LLM'
$out = Join-Path $share 'evidence'
New-Item -ItemType Directory -Force -Path $out | Out-Null

function Say($text) { Write-Output $text }

function Db-Counts($label) {
  $db = Join-Path $dataRoot 'data\alex.db'
  if (-not (Test-Path $db)) { Say "DB $label missing"; return }
  Copy-Item $db (Join-Path $out "db-$label.db") -Force
  Say "DB $label copied $((Get-Item $db).Length)"
}

function Health($label, $port) {
  for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 2
    try {
      $r = Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 3
      if ($r.status -eq 'ok') { Say "HEALTH $label product=$($r.product) version=$($r.version)"; return $r }
    } catch { }
  }
  Say "HEALTH $label failed"
  return $null
}

function Run-Sidecar($label, $port) {
  $exe = Join-Path $install 'sidecar\alex-backend\alex-backend.exe'
  if (-not (Test-Path $exe)) { Say "SIDECAR $label missing"; return $null }
  $env:ALEX_BACKEND_PORT = "$port"
  $env:ALEX_TOR_MANAGED_ENABLED = 'false'
  $p = Start-Process -FilePath $exe -PassThru -WindowStyle Hidden
  $health = Health $label $port
  Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 3
  return $health
}

# ---------------------------------------------------------------- stage 1: 1.2.6 install + data
Say 'STAGE 1 clean install of the 1.2.6 binaries over a seeded 1.2.6 data root'
New-Item -ItemType Directory -Force -Path $install | Out-Null
Copy-Item (Join-Path $share 'payload126\*') $install -Recurse -Force
New-Item -ItemType Directory -Force -Path $dataRoot | Out-Null
Copy-Item (Join-Path $share 'seed126-data\*') $dataRoot -Recurse -Force
Say "INSTALLED_126_EXE $((Get-Item (Join-Path $install 'alex-llm.exe')).VersionInfo.ProductVersion)"
$health126 = Run-Sidecar '126' 18081
Db-Counts 'before-upgrade'

# ---------------------------------------------------------------- stage 2: install 1.2.7
Say 'STAGE 2 run the 1.2.7 installer over the existing install'
$setup = Join-Path $share 'Canalla LLM_1.2.7_x64-setup.exe'
$exit = (Start-Process -FilePath $setup -ArgumentList '/S' -Wait -PassThru).ExitCode
Say "INSTALLER_EXIT $exit"
Start-Sleep -Seconds 5
Say "INSTALLED_127_EXE $((Get-Item (Join-Path $install 'alex-llm.exe')).VersionInfo.ProductVersion)"
Say "INSTALLED_127_SIDECAR $((Get-Item (Join-Path $install 'sidecar\alex-backend\alex-backend.exe')).VersionInfo.ProductVersion)"
$health127 = Run-Sidecar '127' 18082
Db-Counts 'after-upgrade'
$migration = Join-Path $dataRoot 'runtime\migration.json'
if (Test-Path $migration) { Copy-Item $migration (Join-Path $out 'migration.json') -Force; Say 'MIGRATION copied' }

# ---------------------------------------------------------------- stage 3: rollback to 1.2.6
Say 'STAGE 3 rollback: restore the 1.2.6 binaries over the 1.2.7 data'
Copy-Item (Join-Path $share 'payload126\*') $install -Recurse -Force
Say "ROLLBACK_EXE $((Get-Item (Join-Path $install 'alex-llm.exe')).VersionInfo.ProductVersion)"
$healthBack = Run-Sidecar 'rollback' 18083
Db-Counts 'after-rollback'

# ---------------------------------------------------------------- stage 4: uninstall
Say 'STAGE 4 uninstall'
$uninstaller = Join-Path $install 'uninstall.exe'
$exit = (Start-Process -FilePath $uninstaller -ArgumentList '/S' -Wait -PassThru).ExitCode
Start-Sleep -Seconds 8
Say "UNINSTALL_EXIT $exit"
Say "PROGRAM_DIR_EXISTS $([bool](Test-Path $install))"
Say "DATA_ROOT_EXISTS $([bool](Test-Path $dataRoot))"
Db-Counts 'after-uninstall'

# ---------------------------------------------------------------- evidence
$listing = Get-ChildItem $install -Recurse -File -ErrorAction SilentlyContinue |
  Select-Object -First 40 FullName, Length
$listing | Format-Table -AutoSize | Out-String | Set-Content (Join-Path $out 'install-listing.txt')
Say 'DONE'
