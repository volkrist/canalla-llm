$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $repo 'apps\backend'
$python = Join-Path $backend '.venv\Scripts\python.exe'
$sidecarDest = Join-Path $repo 'apps\desktop\src-tauri\sidecar\alex-backend'
if (-not (Test-Path $python)) {
  throw 'BACKEND_START_FAILED: apps/backend/.venv is missing. Run scripts/setup-backend.ps1'
}
Push-Location $backend
try {
  & $python -m pip install -q 'pyinstaller>=6,<7'
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  $dist = Join-Path $backend 'dist\alex-backend'
  if (Test-Path $dist) { Remove-Item -Recurse -Force $dist }
  $build = Join-Path $backend 'build'
  if (Test-Path $build) { Remove-Item -Recurse -Force $build }
  & $python -m PyInstaller --noconfirm --clean (Join-Path $backend 'alex-backend.spec')
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  $exe = Join-Path $dist 'alex-backend.exe'
  if (-not (Test-Path $exe)) { throw "PyInstaller did not produce $exe" }
  # A half-collected native package is worse than an absent one: PyJWT tolerates a missing
  # `cryptography` (ModuleNotFoundError) but crashes on a namespace package that has the binary
  # and none of the Python modules. Never stamp such a bundle.
  $crypto = Join-Path $dist '_internal\cryptography'
  if ((Test-Path $crypto) -and -not (Test-Path (Join-Path $crypto '__init__.py'))) {
    # PyInstaller can place only the native ``hazmat`` payload under ``_internal`` while the pure
    # modules live in the PYZ. That is a valid onedir layout, but the bundle check below needs the
    # package's Python side next to it. Copy it from the exact interpreter that produced the
    # bundle; a source tree that is genuinely missing it still fails loudly.
    $venvRoot = Split-Path (Split-Path $python -Parent) -Parent
    $cryptoSource = Join-Path $venvRoot 'Lib\site-packages\cryptography'
    $cryptoSource = [System.IO.Path]::GetFullPath($cryptoSource)
    if (Test-Path (Join-Path $cryptoSource '__init__.py')) {
      Copy-Item (Join-Path $cryptoSource '*.py') $crypto -Force
    }
  }
  if ((Test-Path $crypto) -and -not (Test-Path (Join-Path $crypto '__init__.py'))) {
    throw "the sidecar bundle has $crypto without __init__.py - half-collected cryptography"
  }
  New-Item -ItemType Directory -Force -Path (Split-Path $sidecarDest) | Out-Null
  if (Test-Path $sidecarDest) { Remove-Item -Recurse -Force $sidecarDest }
  Copy-Item -Recurse $dist $sidecarDest
  # Record what this sidecar was built from, so the bundle step can refuse a stale one instead of
  # shipping a backend that predates the release. See scripts/backend-sidecar-stamp.py.
  & $python (Join-Path $repo 'scripts\backend-sidecar-stamp.py') write `
    --backend $backend `
    --stamp (Join-Path $sidecarDest 'build-stamp.json')
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  $hash = (Get-FileHash $exe -Algorithm SHA256).Hash.ToLowerInvariant()
  $size = (Get-Item $exe).Length
  $sum = Join-Path $repo 'apps\desktop\src-tauri\sidecar\SHA256SUMS.txt'
  @"
alex-backend.exe  $size  sha256:$hash
"@ | Set-Content -Encoding ascii $sum
  Write-Host "SIDECAR $exe"
  Write-Host "SIZE $size"
  Write-Host "SHA256 $hash"
} finally {
  Pop-Location
}
