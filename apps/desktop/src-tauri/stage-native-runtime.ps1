$ErrorActionPreference = 'Stop'
$srcTauri = $PSScriptRoot
$sidecar = Join-Path $srcTauri 'sidecar'
New-Item -ItemType Directory -Force -Path $sidecar | Out-Null
$candidates = @(
  (Join-Path $srcTauri 'target\release\alex-host-loop.exe'),
  (Join-Path $srcTauri '..\target\release\alex-host-loop.exe')
)
$source = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $source) {
  throw "NATIVE_HOST_MISSING cwd=$(Get-Location) srcTauri=$srcTauri"
}
Copy-Item -Force $source (Join-Path $sidecar 'alex-host-loop.exe')
Write-Host "HOST $source"
