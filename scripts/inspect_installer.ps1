# Mechanical installed-app checks. No GPU. No TinyFish.
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$setup = Get-ChildItem -Recurse (Join-Path $repo 'apps\desktop\src-tauri\target\release\bundle\nsis') -Filter '*setup.exe' |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
if (-not $setup) { throw 'INSTALLER_MISSING' }
Write-Host "INSTALLER $($setup.FullName)"
Write-Host "SIZE $((Get-Item $setup.FullName).Length)"
Write-Host "SHA256 $((Get-FileHash $setup.FullName -Algorithm SHA256).Hash.ToLowerInvariant())"
$installDir = Join-Path $env:LOCALAPPDATA 'Programs\Canalla LLM'
if (-not (Test-Path $installDir)) {
  # Pre-rename installation (product name changed in 0.9.3; the data root never moved).
  $installDir = Join-Path $env:LOCALAPPDATA 'Programs\Alex LLM'
}
Write-Host "Installing silently"
Start-Process -FilePath $setup.FullName -ArgumentList '/S' -Wait
$desktopExe = @('alex-llm.exe', 'Canalla LLM.exe', 'Alex LLM.exe') |
  ForEach-Object { Join-Path $installDir $_ } |
  Where-Object { Test-Path $_ } |
  Select-Object -First 1
if (-not $desktopExe) {
  $desktopExe = Get-ChildItem $installDir -Filter '*.exe' -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match 'alex-llm|[Cc]analla LLM|Alex LLM' -and $_.Name -notmatch 'uninstall|backend|host' } |
    Select-Object -First 1 -ExpandProperty FullName
}
if (-not $desktopExe -or -not (Test-Path $desktopExe)) { throw 'INSTALLED_EXE_MISSING' }
Write-Host "INSTALLED_EXE $desktopExe"
$sidecar = Get-ChildItem (Split-Path $desktopExe) -Recurse -Filter 'alex-backend.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
Write-Host "INSTALLED_SIDECAR $($sidecar.FullName)"
$hostLoop = Get-ChildItem (Split-Path $desktopExe) -Recurse -Filter 'alex-host-loop.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
Write-Host "INSTALLED_HOST $($hostLoop.FullName)"
