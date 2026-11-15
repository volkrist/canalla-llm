# Isolated Windows installer acceptance on the host: clean install, data preservation,
# rollback, uninstall — and a verified restore of the user's 1.2.6 installation afterwards.
#
# Production is snapshotted first (files, uninstall key, Start Menu shortcut) and verified at the
# end. The installer runs with /D into D:\canalla-vm\host-accept\install, and the uninstall key is
# temporarily removed so NSIS sees a clean machine instead of upgrading the user's install.
$ErrorActionPreference = 'Stop'
$root = 'D:\canalla-vm\host-accept'
$install = Join-Path $root 'install'
$data = Join-Path $root 'data'
$evidence = Join-Path $root 'evidence'
$snapshot = 'D:\canalla-vm\prod-snapshot'
$share = 'D:\canalla-vm\share'

$prodInstall = Join-Path $env:LOCALAPPDATA 'Programs\Canalla LLM'
$prodShortcut = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Canalla LLM.lnk'
$uninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Canalla LLM'
$softwareKey = 'HKCU:\Software\Canalla LLM'

New-Item -ItemType Directory -Force -Path $root, $evidence, $snapshot | Out-Null

function Say($text) { Write-Output $text; Add-Content -Path (Join-Path $evidence 'run.log') -Value $text }

function Hash($path) { if (Test-Path $path) { (Get-FileHash $path -Algorithm SHA256).Hash.ToLowerInvariant() } else { 'missing' } }

function Health($label, $port) {
  for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 2
    try {
      $r = Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 3
      if ($r.status -eq 'ok') { Say "HEALTH $label version=$($r.version) product=$($r.product)"; return $r }
    } catch { }
  }
  Say "HEALTH $label FAILED"
  return $null
}

function RunInstalledSidecar($label, $dir, $port, $dataDir) {
  $exe = Join-Path $dir 'sidecar\alex-backend\alex-backend.exe'
  if (-not (Test-Path $exe)) { Say "SIDECAR $label missing at $exe"; return $null }
  $env:ALEX_LLM_DATA_DIR = $dataDir
  $env:ALEX_BACKEND_PORT = "$port"
  $env:ALEX_TOR_MANAGED_ENABLED = 'false'
  $process = Start-Process -FilePath $exe -PassThru -WindowStyle Hidden
  $health = Health $label $port
  Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 3
  $db = Join-Path $dataDir 'data\alex.db'
  if (Test-Path $db) { Copy-Item $db (Join-Path $evidence "db-$label.db") -Force; Say "DB $label $((Get-Item $db).Length) bytes" }
  return $health
}

# ------------------------------------------------------------------ snapshot
Say 'SNAPSHOT production'
robocopy $prodInstall (Join-Path $snapshot 'program') /E /NFL /NDL /NJH /NJS /NP | Out-Null
reg.exe export $uninstallKey (Join-Path $snapshot 'canalla-llm.reg') /y | Out-Null
if (Test-Path $softwareKey) { reg.exe export $softwareKey (Join-Path $snapshot 'canalla-llm-software.reg') /y | Out-Null }
if (Test-Path $prodShortcut) { Copy-Item $prodShortcut (Join-Path $snapshot 'Canalla LLM.lnk') -Force }
Copy-Item 'D:\canalla-vm\share\seed126-data' (Join-Path $root 'seed126-data') -Recurse -Force
$prodExeHash = Hash (Join-Path $prodInstall 'alex-llm.exe')
Say "PROD_EXE_SHA256 $prodExeHash"
Say "PROD_FILES $((Get-ChildItem $prodInstall -Recurse -File | Measure-Object).Count)"

# ------------------------------------------------------------------ clean install 1.2.7
Say 'INSTALL 1.2.7 into an isolated directory'
Remove-Item $uninstallKey -Recurse -Force -ErrorAction SilentlyContinue
$setup = Join-Path $share 'Canalla LLM_1.2.7_x64-setup.exe'
$exit = (Start-Process -FilePath $setup -ArgumentList '/S', "/D=$install" -Wait -PassThru).ExitCode
Say "INSTALL_EXIT $exit"
Say "INSTALLED_VERSION $((Get-Item (Join-Path $install 'alex-llm.exe')).VersionInfo.ProductVersion)"
Say "INSTALLED_FILES $((Get-ChildItem $install -Recurse -File | Measure-Object).Count)"
Get-ChildItem $install -Recurse -File | Select-Object FullName, Length |
  Out-String | Set-Content (Join-Path $evidence 'install-listing.txt')

# ------------------------------------------------------------------ data preservation
Say 'DATA: seed the 1.2.6 data root and run the 1.2.7 sidecar against it'
Copy-Item (Join-Path $root 'seed126-data') $data -Recurse -Force
$healthNew = RunInstalledSidecar '127-with-126-data' $install 18091 $data
$migration = Join-Path $data 'runtime\migration.json'
if (Test-Path $migration) { Copy-Item $migration (Join-Path $evidence 'migration.json') -Force }

# ------------------------------------------------------------------ rollback
Say 'ROLLBACK: restore the 1.2.6 binaries over the 1.2.7 install'
Copy-Item (Join-Path $share 'payload126\*') $install -Recurse -Force
Say "ROLLBACK_VERSION $((Get-Item (Join-Path $install 'alex-llm.exe')).VersionInfo.ProductVersion)"
$healthBack = RunInstalledSidecar '126-after-rollback' $install 18092 $data

# ------------------------------------------------------------------ uninstall
Say 'UNINSTALL the isolated installation'
$uninstaller = Join-Path $install 'uninstall.exe'
if (Test-Path $uninstaller) {
  $exit = (Start-Process -FilePath $uninstaller -ArgumentList '/S' -Wait -PassThru).ExitCode
  Say "UNINSTALL_EXIT $exit"
} else { Say 'UNINSTALL_EXIT no uninstaller' }
Start-Sleep -Seconds 8
Say "AFTER_UNINSTALL_PROGRAM_DIR $([bool](Test-Path $install))"
Say "AFTER_UNINSTALL_DATA_DIR $([bool](Test-Path (Join-Path $data 'data\alex.db')))"

# ------------------------------------------------------------------ restore production
Say 'RESTORE production snapshot'
Remove-Item $uninstallKey -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $softwareKey -Recurse -Force -ErrorAction SilentlyContinue
reg.exe import (Join-Path $snapshot 'canalla-llm.reg') | Out-Null
if (Test-Path (Join-Path $snapshot 'canalla-llm-software.reg')) { reg.exe import (Join-Path $snapshot 'canalla-llm-software.reg') | Out-Null }
if (Test-Path (Join-Path $snapshot 'Canalla LLM.lnk')) {
  Copy-Item (Join-Path $snapshot 'Canalla LLM.lnk') $prodShortcut -Force
}
robocopy (Join-Path $snapshot 'program') $prodInstall /E /NFL /NDL /NJH /NJS /NP | Out-Null
$afterHash = Hash (Join-Path $prodInstall 'alex-llm.exe')
Say "PROD_EXE_SHA256_AFTER $afterHash"
Say "PROD_RESTORED $($afterHash -eq $prodExeHash)"
Say "PROD_FILES_AFTER $((Get-ChildItem $prodInstall -Recurse -File | Measure-Object).Count)"
$check = Get-ItemProperty $uninstallKey -ErrorAction SilentlyContinue
Say "PROD_UNINSTALL_KEY $($check.DisplayVersion) $($check.InstallLocation)"
Say 'DONE'
