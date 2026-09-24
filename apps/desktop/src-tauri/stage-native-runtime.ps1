$ErrorActionPreference = 'Stop'
$srcTauri = $PSScriptRoot
$repo = (Resolve-Path (Join-Path $srcTauri '..\..\..')).Path
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

# The bundle must not carry a backend built from an older source tree. That already shipped once: the
# 1.2.0 candidate installer advertised 1.2.0 while its packaged backend answered /health with 1.1.0,
# because this hook checked only that a sidecar existed. A warning in a build log is a warning nobody
# reads, so a stale stamp stops the bundle here.
$python = Join-Path $repo 'apps\backend\.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
  $python = (Get-Command python -ErrorAction SilentlyContinue).Source
}
if (-not $python) {
  throw 'BACKEND_SIDECAR_STAMP_UNCHECKED: no python to verify the packaged backend with'
}
& $python (Join-Path $repo 'scripts\backend-sidecar-stamp.py') check `
  --backend (Join-Path $repo 'apps\backend') `
  --stamp (Join-Path $srcTauri 'sidecar\alex-backend\build-stamp.json')
if ($LASTEXITCODE -ne 0) {
  throw 'BACKEND_SIDECAR_STALE: rebuild it with scripts\build-backend-sidecar.ps1 before bundling'
}
Write-Host 'SIDECAR stamp is current'
