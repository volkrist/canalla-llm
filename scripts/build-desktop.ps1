param(
  # A test build never touches the production updater signing key and is never published. It is
  # otherwise identical to a release build, so what it produces is a real installer to install and
  # upgrade with.
  [switch]$TestBuild
)
$ErrorActionPreference = 'Stop'
$cargoBin = Join-Path $env:USERPROFILE '.cargo\bin'
if (Test-Path $cargoBin) { $env:PATH = "$cargoBin;$env:PATH" }
$repo = Split-Path -Parent $PSScriptRoot
& (Join-Path $PSScriptRoot 'build-backend-sidecar.ps1')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
# The Tor daemon the product ships. It is fetched, verified against the pin in
# scripts/tor-runtime.json and staged into the bundle resources; a hash that does not match stops
# the build here instead of shipping an unexpected runtime.
$python = Join-Path $repo 'apps\backend\.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw "PYTHON_MISSING: $python" }
& $python (Join-Path $PSScriptRoot 'fetch-tor-runtime.py')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
# Updater signing: the private key never lives in the repository. Point
# TAURI_SIGNING_PRIVATE_KEY(_PATH) at it, or keep it at the operator's own path below.
#
# Tauri's bundler prompts for a password when the password variable is missing, and a prompt hangs an
# automated build (on Windows it reads the console directly, so redirecting stdin does not help).
# The split is therefore explicit: with a password-protected key the bundler signs the artifact
# itself, and without one the bundler step is skipped and the installer is signed right here. The
# updater accepts the installer as its artifact, so the signature is the same either way.
#
# `-TestBuild` skips this whole block: no key is read and no signature is produced.
$bundlerSigns = $false
if (-not $TestBuild) {
  if (-not $env:TAURI_SIGNING_PRIVATE_KEY -and -not $env:TAURI_SIGNING_PRIVATE_KEY_PATH) {
    $candidate = Join-Path $env:USERPROFILE '.canalla-updater\canalla-updater-test.key'
    if (Test-Path $candidate) { $env:TAURI_SIGNING_PRIVATE_KEY_PATH = $candidate }
  }
  if (-not $env:TAURI_SIGNING_PRIVATE_KEY -and -not $env:TAURI_SIGNING_PRIVATE_KEY_PATH) {
    throw 'UPDATER_SIGNING_KEY_MISSING: set TAURI_SIGNING_PRIVATE_KEY_PATH before a release build'
  }
  if ($env:TAURI_SIGNING_PRIVATE_KEY_PATH) {
    $resolved = Resolve-Path $env:TAURI_SIGNING_PRIVATE_KEY_PATH -ErrorAction SilentlyContinue
    if ($resolved) { $env:TAURI_SIGNING_PRIVATE_KEY_PATH = $resolved.Path }
  }
  $bundlerSigns = [bool]($env:TAURI_SIGNING_PRIVATE_KEY -and $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD)
}
$srcTauri = Join-Path $repo 'apps\desktop\src-tauri'
Push-Location $srcTauri
try {
  cargo build --release --bin alex-host-loop
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally { Pop-Location }
& (Join-Path $PSScriptRoot 'stage-native-runtime.ps1')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
# The compiled enrollment default. ``gateway.rs`` reads it with ``option_env!``, so it is baked at
# compile time and may be overridden at runtime — but a release build must never omit it: the
# 1.2.0 installer shipped without it and every fresh install showed an empty «Адрес Gateway» with
# a disabled «Подключить». An explicit value wins, so a test build can still point elsewhere.
if (-not $env:ALEX_GATEWAY_URL) { $env:ALEX_GATEWAY_URL = 'https://gateway.12testers.store' }
Write-Host ('ALEX_GATEWAY_URL=' + $env:ALEX_GATEWAY_URL)
Push-Location (Join-Path $repo 'apps\desktop')
try {
  if ($bundlerSigns) {
    npm run tauri build
  } else {
    $override = Join-Path $env:TEMP 'canalla-build-without-updater-signing.json'
    '{"bundle":{"createUpdaterArtifacts":false}}' | Set-Content -Encoding ascii $override
    npm run tauri build -- --config $override
    if ($LASTEXITCODE -eq 0 -and -not $TestBuild) {
      $built = Get-ChildItem (Join-Path $srcTauri 'target\release\bundle\nsis') -Filter '*setup.exe' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
      if (-not $built) { throw 'INSTALLER_MISSING: the bundle step produced no setup.exe' }
      # cmd preserves the empty password argument; PowerShell 5.1 drops it, and a missing -p would
      # make the CLI prompt and hang the build.
      cmd /c ('npx tauri signer sign -f "{0}" -p "" "{1}"' -f $env:TAURI_SIGNING_PRIVATE_KEY_PATH, $built.FullName)
      if ($LASTEXITCODE -ne 0) { throw 'UPDATER_SIGNING_FAILED: the installer was not signed' }
      Write-Host ('SIGNED ' + $built.FullName + '.sig')
    }
    if ($TestBuild) { Write-Host 'TEST BUILD: no updater signing key was read and no signature was produced' }
  }
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally { Pop-Location }
$nsis = Get-ChildItem -Recurse (Join-Path $repo 'apps\desktop\src-tauri\target\release\bundle\nsis') -Filter '*setup.exe' -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
$desktop = @(
  (Join-Path $repo 'apps\desktop\src-tauri\target\release\alex-llm.exe'),
  (Join-Path $repo 'apps\desktop\src-tauri\target\release\Canalla LLM.exe'),
  (Join-Path $repo 'apps\desktop\src-tauri\target\release\Alex LLM.exe')
) | Where-Object { Test-Path $_ } | Select-Object -First 1
# The guard for exactly the 1.2.0 defect: the built executable must carry the production Gateway
# URL as a standalone literal, or the build stops here instead of shipping a client that cannot
# enroll without a human typing the address.
if (-not $desktop) { throw 'PACKAGED_EXE_MISSING: no built desktop executable to verify' }
& $python (Join-Path $PSScriptRoot 'verify-packaged-gateway-url.py') $desktop
if ($LASTEXITCODE -ne 0) { throw 'GATEWAY_URL_NOT_BAKED: the client would not know its Gateway' }
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
$torBinary = Join-Path $srcTauri 'runtime\tor\tor.exe'
if (Test-Path $torBinary) { Write-Hash $torBinary }
