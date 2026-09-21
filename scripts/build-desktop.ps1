$ErrorActionPreference = 'Stop'
$cargoBin = Join-Path $env:USERPROFILE '.cargo\bin'
if (Test-Path $cargoBin) { $env:PATH = "$cargoBin;$env:PATH" }
$repo = Split-Path -Parent $PSScriptRoot
& (Join-Path $PSScriptRoot 'build-backend-sidecar.ps1')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$srcTauri = Join-Path $repo 'apps\desktop\src-tauri'
Push-Location $srcTauri
try {
  cargo build --release --bin alex-host-loop
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally { Pop-Location }
& (Join-Path $PSScriptRoot 'stage-native-runtime.ps1')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Push-Location (Join-Path $repo 'apps\desktop')
try {
  npm run tauri build
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally { Pop-Location }
$nsis = Get-ChildItem -Recurse (Join-Path $repo 'apps\desktop\src-tauri\target\release\bundle\nsis') -Filter '*setup.exe' -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
$desktop = @(
  (Join-Path $repo 'apps\desktop\src-tauri\target\release\alex-llm.exe'),
  (Join-Path $repo 'apps\desktop\src-tauri\target\release\Alex LLM.exe')
) | Where-Object { Test-Path $_ } | Select-Object -First 1
$sidecar = Join-Path $repo 'apps\desktop\src-tauri\sidecar\alex-backend\alex-backend.exe'
$sum = Join-Path $repo 'apps\desktop\src-tauri\sidecar\SHA256SUMS.txt'
function Write-Hash($path) {
  if (-not (Test-Path $path)) { return }
  $hash = (Get-FileHash $path -Algorithm SHA256).Hash.ToLowerInvariant()
  $size = (Get-Item $path).Length
  Add-Content -Encoding ascii $sum ("{0}  {1}  sha256:{2}" -f (Split-Path $path -Leaf), $size, $hash)
  Write-Host ("{0} SIZE {1} SHA256 {2}" -f $path, $size, $hash)
}
Write-Hash $desktop
if ($nsis) { Write-Hash $nsis.FullName }
Write-Hash $sidecar
