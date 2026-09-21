$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$srcTauri = Join-Path $repo 'apps\desktop\src-tauri'
$sidecar = Join-Path $srcTauri 'sidecar'
New-Item -ItemType Directory -Force -Path $sidecar | Out-Null
$candidates = @(
  (Join-Path $srcTauri 'target\release\alex-host-loop.exe'),
  (Join-Path $repo 'apps\desktop\target\release\alex-host-loop.exe')
)
$source = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $source) {
  throw 'NATIVE_HOST_MISSING: alex-host-loop.exe was not built. Run tauri/cargo release first.'
}
Copy-Item -Force $source (Join-Path $sidecar 'alex-host-loop.exe')
$hash = (Get-FileHash $source -Algorithm SHA256).Hash.ToLowerInvariant()
$size = (Get-Item $source).Length
$sum = Join-Path $sidecar 'SHA256SUMS.txt'
Add-Content -Encoding ascii $sum "alex-host-loop.exe  $size  sha256:$hash"
Write-Host "HOST $source"
Write-Host "SIZE $size"
Write-Host "SHA256 $hash"
