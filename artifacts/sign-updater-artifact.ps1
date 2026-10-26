# Safe, local production-updater signing for the already-built 1.2.4 installer.
#
# The production private key exists at the path below, but its password is known only to the owner.
# This script never stores, prints or passes the password on the command line: it prompts with a
# hidden SecureString, puts it in TAURI_SIGNING_PRIVATE_KEY_PASSWORD for the child process only,
# and clears both the environment variable and the BSTR afterwards.
#
# Run it yourself, from an interactive PowerShell:
#   powershell -NoProfile -ExecutionPolicy Bypass -File artifacts\sign-updater-artifact.ps1
#
# It produces "<installer>.sig" and then runs the release gate's verify-artifact, which checks the
# signature against the PUBLIC key configured in tauri.conf.json. It does not publish anything.

param(
  [string]$Installer = 'C:\Users\Volkr\Documents\Codex\2026-09-13\x20\outputs\alex-llm\apps\desktop\src-tauri\target\release\bundle\nsis\Canalla LLM_1.2.4_x64-setup.exe',
  [string]$KeyPath = 'C:\Users\Volkr\.canalla-updater\production\canalla-updater-production.key'
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo 'apps\backend\.venv\Scripts\python.exe'

if (-not (Test-Path $Installer)) { throw "INSTALLER_MISSING: $Installer" }
if (-not (Test-Path $KeyPath)) { throw "KEY_MISSING: $KeyPath" }
if (-not (Test-Path $python)) { throw "PYTHON_MISSING: $python" }

$secure = Read-Host -AsSecureString 'Production updater key password (input is hidden)'
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
  $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
  $env:TAURI_SIGNING_PRIVATE_KEY_PATH = $KeyPath
  Push-Location (Join-Path $repo 'apps\desktop')
  try {
    # No -p: the signer reads TAURI_SIGNING_PRIVATE_KEY_PASSWORD from the environment.
    & npx tauri signer sign -f $KeyPath $Installer
    if ($LASTEXITCODE -ne 0) { throw 'SIGNING_FAILED: the signer rejected the key or the password' }
  } finally {
    Pop-Location
  }
} finally {
  [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
  Remove-Item Env:\TAURI_SIGNING_PRIVATE_KEY_PASSWORD -ErrorAction SilentlyContinue
  Remove-Item Env:\TAURI_SIGNING_PRIVATE_KEY_PATH -ErrorAction SilentlyContinue
}

$sig = $Installer + '.sig'
if (-not (Test-Path $sig)) { throw "SIG_MISSING: $sig" }

Write-Output ('INSTALLER_SHA256 ' + (Get-FileHash $Installer -Algorithm SHA256).Hash.ToLowerInvariant())
Write-Output ('SIG ' + $sig)
Write-Output ('SIG_BYTES ' + (Get-Item $sig).Length)

# The same public-key check the release gate performs. No secret is involved.
& $python (Join-Path $repo 'scripts\release-gate.py') verify-artifact `
  --installer $Installer --version 1.2.4 --gateway-url https://gateway.12testers.store
exit $LASTEXITCODE
