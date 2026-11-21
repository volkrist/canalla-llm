# Clean-install acceptance on the host, isolated by a temporary data root and a temporary device
# credential target, with the user's installation renamed away and restored afterwards.
#
# Why not a VM: the host has no free Windows desktop environment (VirtualBox + Hyper-V, 8 GB RAM)
# and the CI token cannot add a workflow. This method proves the same behaviour on the real
# installer while touching no user data: the programme directory is renamed (never deleted) before
# the install and put back at the end, and every registry key is exported first.
$ErrorActionPreference = 'Stop'
$root = 'D:\canalla-vm\clean-install'
$evidence = Join-Path $root 'evidence'
$setup = 'D:\canalla-vm\share\Canalla LLM_1.2.7_x64-setup.exe'
$prodInstall = Join-Path $env:LOCALAPPDATA 'Programs\Canalla LLM'
$stash = Join-Path $env:LOCALAPPDATA 'Programs\Canalla LLM.prod-backup'
$dataRoot = Join-Path $root 'data'
$uninstallKey = 'HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\Canalla LLM'
$softwareKey = 'HKCU\Software\Canalla LLM'
$runKey = 'HKCU\Software\Microsoft\Windows\CurrentVersion\Run'
New-Item -ItemType Directory -Force -Path $root, $evidence | Out-Null

function Say($text) { Write-Output $text; Add-Content -Path (Join-Path $evidence 'clean-install.log') -Value $text }
function Hash($path) { if (Test-Path $path) { (Get-FileHash $path -Algorithm SHA256).Hash.ToLowerInvariant() } else { 'missing' } }

function Health($label, $port, $dataDir) {
  $exe = Join-Path $prodInstall 'sidecar\alex-backend\alex-backend.exe'
  if (-not (Test-Path $exe)) { Say "HEALTH $label missing sidecar"; return }
  $env:ALEX_LLM_DATA_DIR = $dataDir
  $env:ALEX_BACKEND_PORT = "$port"
  $env:ALEX_TOR_MANAGED_ENABLED = 'false'
  $env:ALEX_DEVICE_CREDENTIAL_TARGET = 'CanallaLLM/Acceptance/CleanInstall'
  $process = Start-Process -FilePath $exe -PassThru -WindowStyle Hidden
  $health = $null
  for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 2
    try {
      $health = Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 3
      if ($health.status -eq 'ok') { break }
    } catch { $health = $null }
  }
  if ($null -eq $health) { Say "HEALTH $label FAILED" } else { Say "HEALTH $label version=$($health.version) product=$($health.product)" }
  Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 3
}

# ------------------------------------------------------------------ 0. no running process
$running = Get-Process -Name alex-llm,alex-backend,alex-host-loop -ErrorAction SilentlyContinue
if ($running) { throw 'Canalla is running; refusing to touch the installation' }

# ------------------------------------------------------------------ 1. snapshot
Say 'SNAPSHOT'
robocopy $prodInstall (Join-Path $root 'program-backup') /E /NFL /NDL /NJH /NJS /NP | Out-Null
reg.exe export $uninstallKey (Join-Path $root 'uninstall.reg') /y | Out-Null
if (Test-Path 'HKCU:\Software\Canalla LLM') { reg.exe export $softwareKey (Join-Path $root 'software.reg') /y | Out-Null }
reg.exe export $runKey (Join-Path $root 'run.reg') /y | Out-Null
Say "PROD_EXE $(Hash (Join-Path $prodInstall 'alex-llm.exe'))"
Say "PROD_FILES $((Get-ChildItem $prodInstall -Recurse -File | Measure-Object).Count)"
$shortcut = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Canalla LLM.lnk'
if (Test-Path $shortcut) { Copy-Item $shortcut (Join-Path $root 'shortcut.lnk') -Force }

# ------------------------------------------------------------------ 2. make the machine look new
Say 'RENAME the installed programme away (a copy is kept, nothing is deleted)'
Rename-Item $prodInstall $stash
Remove-Item 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Canalla LLM' -Recurse -Force -ErrorAction SilentlyContinue

# ------------------------------------------------------------------ 3. clean install
Say 'INSTALL 1.2.7 (clean)'
$exit = (Start-Process -FilePath $setup -ArgumentList '/S' -Wait -PassThru).ExitCode
Start-Sleep -Seconds 5
Say "INSTALL_EXIT $exit"
Say "FRESH_EXE_VERSION $((Get-Item (Join-Path $prodInstall 'alex-llm.exe')).VersionInfo.ProductVersion)"
Say "FRESH_FILES $((Get-ChildItem $prodInstall -Recurse -File | Measure-Object).Count)"
$key = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Canalla LLM' -ErrorAction SilentlyContinue
Say "FRESH_REGISTRY $($key.DisplayVersion) $($key.InstallLocation)"
Say "TOR_PRESENT $(Test-Path (Join-Path $prodInstall 'runtime\tor\tor.exe'))"
Say "HOST_PRESENT $(Test-Path (Join-Path $prodInstall 'alex-host-loop.exe'))"

# ------------------------------------------------------------------ 4. first run and restart
Say 'FIRST RUN with an isolated data root'
Health 'clean-first' 18101 $dataRoot
$db = Join-Path $dataRoot 'data\alex.db'
Say "DATA_ROOT_CREATED $(Test-Path $db)"
Say 'CLOSE and START AGAIN'
Health 'clean-restart' 18102 $dataRoot
Say "DATA_ROOT_SURVIVED $(Test-Path $db)"
$counts = ''
if (Test-Path $db) {
  Copy-Item $db (Join-Path $evidence 'clean-data.db') -Force
  $counts = (Get-Item $db).Length
}
Say "CLEAN_DB_BYTES $counts"
Say "NO_LEFTOVER_PROCESSES $((Get-Process -Name alex-backend,alex-llm,alex-host-loop -ErrorAction SilentlyContinue | Measure-Object).Count -eq 0)"

# ------------------------------------------------------------------ 5. uninstall
Say 'UNINSTALL'
$uninstaller = Join-Path $prodInstall 'uninstall.exe'
$exit = (Start-Process -FilePath $uninstaller -ArgumentList '/S' -Wait -PassThru).ExitCode
Start-Sleep -Seconds 10
Say "UNINSTALL_EXIT $exit"
Say "PROGRAM_DIR_AFTER $([bool](Test-Path $prodInstall))"
if (Test-Path $prodInstall) {
  Get-ChildItem $prodInstall -Recurse -File -ErrorAction SilentlyContinue | Select-Object -First 15 FullName |
    ForEach-Object { Say "LEFT $($_.FullName)" }
}
$hookLog = Join-Path $env:TEMP 'canalla-uninstall.log'
if (Test-Path $hookLog) {
  Get-Content $hookLog | ForEach-Object { Say "HOOK $_" }
} else {
  Say 'HOOK log missing'
}
Say "DATA_ROOT_AFTER $([bool](Test-Path $db))"
Say "REGISTRY_AFTER $([bool](Test-Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Canalla LLM'))"
Say "LEFTOVER_PROCESSES_AFTER $((Get-Process -Name alex-backend,alex-llm,alex-host-loop -ErrorAction SilentlyContinue | Measure-Object).Count)"

# ------------------------------------------------------------------ 6. restore the user's install
Say 'RESTORE'
# Defensive: after a partially successful uninstall a process may still hold a file inside the
# test installation. Stop only processes whose image lives there.
Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($prodInstall, [StringComparison]::OrdinalIgnoreCase) } | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3
if (Test-Path $prodInstall) { Remove-Item $prodInstall -Recurse -Force -ErrorAction SilentlyContinue }
Rename-Item $stash $prodInstall
reg.exe import (Join-Path $root 'uninstall.reg') | Out-Null
if (Test-Path (Join-Path $root 'software.reg')) { reg.exe import (Join-Path $root 'software.reg') | Out-Null }
reg.exe import (Join-Path $root 'run.reg') | Out-Null
if (Test-Path (Join-Path $root 'shortcut.lnk')) { Copy-Item (Join-Path $root 'shortcut.lnk') $shortcut -Force }
$restored = Hash (Join-Path $prodInstall 'alex-llm.exe')
Say "RESTORED_EXE $restored"
Say "RESTORED_MATCHES $($restored -eq '21b50e462156b9f109303623e4c67225d4e44daad981d8f439a83d16c8cafaf1')"
Say "RESTORED_VERSION $((Get-Item (Join-Path $prodInstall 'alex-llm.exe')).VersionInfo.ProductVersion)"
Say "RESTORED_FILES $((Get-ChildItem $prodInstall -Recurse -File | Measure-Object).Count)"
$restoredKey = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Canalla LLM'
Say "RESTORED_REGISTRY $($restoredKey.DisplayVersion)"
Say 'DONE'
