# Finish the restore the clean-install run could not: the uninstaller left a running tor.exe
# inside the test installation, which locked the directory and stopped the rename back.
$ErrorActionPreference = 'Continue'
$root = 'D:\canalla-vm\clean-install'
$evidence = Join-Path $root 'evidence'
$prodInstall = Join-Path $env:LOCALAPPDATA 'Programs\Canalla LLM'
$stash = Join-Path $env:LOCALAPPDATA 'Programs\Canalla LLM.prod-backup'
$shortcut = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Canalla LLM.lnk'
function Say($text) { Write-Output $text; Add-Content -Path (Join-Path $evidence 'restore.log') -Value $text }

Say 'STOP the test tor.exe'
Get-Process -Name tor -ErrorAction SilentlyContinue | ForEach-Object {
  if ($_.Path -like "$prodInstall*") {
    Say "stopping pid=$($_.Id) $($_.Path)"
    Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
  }
}
Start-Sleep -Seconds 3

Say 'REMOVE the test programme directory'
if (Test-Path $prodInstall) {
  Remove-Item $prodInstall -Recurse -Force -ErrorAction SilentlyContinue
}
Say "TEST_DIR_REMOVED $(-not (Test-Path $prodInstall))"

Say 'RESTORE the user installation'
if (Test-Path $stash) {
  if (Test-Path $prodInstall) { Remove-Item $prodInstall -Recurse -Force -ErrorAction SilentlyContinue }
  Rename-Item $stash $prodInstall
}
reg.exe import (Join-Path $root 'uninstall.reg') | Out-Null
if (Test-Path (Join-Path $root 'software.reg')) { reg.exe import (Join-Path $root 'software.reg') | Out-Null }
reg.exe import (Join-Path $root 'run.reg') | Out-Null
if (Test-Path (Join-Path $root 'shortcut.lnk')) { Copy-Item (Join-Path $root 'shortcut.lnk') $shortcut -Force }

$exe = Join-Path $prodInstall 'alex-llm.exe'
$hash = if (Test-Path $exe) { (Get-FileHash $exe -Algorithm SHA256).Hash.ToLowerInvariant() } else { 'missing' }
Say "RESTORED_EXE $hash"
Say "RESTORED_MATCHES $($hash -eq '21b50e462156b9f109303623e4c67225d4e44daad981d8f439a83d16c8cafaf1')"
Say "RESTORED_VERSION $((Get-Item $exe -ErrorAction SilentlyContinue).VersionInfo.ProductVersion)"
Say "RESTORED_FILES $((Get-ChildItem $prodInstall -Recurse -File -ErrorAction SilentlyContinue | Measure-Object).Count)"
$key = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Canalla LLM' -ErrorAction SilentlyContinue
Say "RESTORED_REGISTRY $($key.DisplayVersion) $($key.InstallLocation)"
Say "SHORTCUT $(Test-Path $shortcut)"
Say 'DONE'
