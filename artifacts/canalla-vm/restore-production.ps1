# Emergency restore: the 1.2.7 installer ignored /D and upgraded the real installation.
# 1) preserve the 1.2.7 payload for the data-compatibility test,
# 2) put the user's 1.2.6 files back from the pre-test snapshot and verify them byte for byte,
# 3) restore the uninstall key's version field.
$ErrorActionPreference = 'Stop'
$snapshot = 'D:\canalla-vm\prod-snapshot\program'
$prodInstall = Join-Path $env:LOCALAPPDATA 'Programs\Canalla LLM'
$payload127 = 'D:\canalla-vm\host-accept\payload127'
$evidence = 'D:\canalla-vm\host-accept\evidence'
New-Item -ItemType Directory -Force -Path $payload127, $evidence | Out-Null

function Say($text) { Write-Output $text; Add-Content -Path (Join-Path $evidence 'restore.log') -Value $text }
function Hash($path) { if (Test-Path $path) { (Get-FileHash $path -Algorithm SHA256).Hash.ToLowerInvariant() } else { 'missing' } }

Say 'PRESERVE 1.2.7 payload'
robocopy $prodInstall $payload127 /E /NFL /NDL /NJH /NJS /NP | Out-Null
Say "PAYLOAD_127_VERSION $((Get-Item (Join-Path $payload127 'alex-llm.exe')).VersionInfo.ProductVersion)"
Say "PAYLOAD_127_SHA256 $(Hash (Join-Path $payload127 'alex-llm.exe'))"

Say 'RESTORE 1.2.6 files from the snapshot'
robocopy $snapshot $prodInstall /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
$restored = Hash (Join-Path $prodInstall 'alex-llm.exe')
Say "RESTORED_EXE_SHA256 $restored"
Say "RESTORED_EXPECTED 21b50e462156b9f109303623e4c67225d4e44daad981d8f439a83d16c8cafaf1"
Say "RESTORED_MATCHES $($restored -eq '21b50e462156b9f109303623e4c67225d4e44daad981d8f439a83d16c8cafaf1')"
Say "RESTORED_FILES $((Get-ChildItem $prodInstall -Recurse -File | Measure-Object).Count)"
Say "RESTORED_VERSION $((Get-Item (Join-Path $prodInstall 'alex-llm.exe')).VersionInfo.ProductVersion)"

Say 'RESTORE the uninstall key version'
$key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Canalla LLM'
if (Test-Path $key) {
  Set-ItemProperty -Path $key -Name 'DisplayVersion' -Value '1.2.6'
  $k = Get-ItemProperty $key
  Say "UNINSTALL_KEY $($k.DisplayVersion) $($k.InstallLocation)"
} else {
  Say 'UNINSTALL_KEY missing'
}
if (-not (Test-Path 'HKCU:\Software\Canalla LLM')) {
  New-Item -Path 'HKCU:\Software\Canalla LLM' | Out-Null
  Say 'SOFTWARE_KEY recreated empty'
}
Say 'DONE'
