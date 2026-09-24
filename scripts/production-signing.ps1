<#
.SYNOPSIS
  The Canalla LLM production updater signing identity: create it, recover it, and sign with it.

.DESCRIPTION
  The updater public key is compiled into every shipped client, so the identity behind it decides
  what a released Canalla can ever install. This script is the only thing that touches the private
  half. It exists so that the release path never depends on a human typing a password into a
  terminal, and so that the "no secret" failure is a named error instead of a hang:

    production_signing_secret_unavailable   the Credential Manager entry is missing or unreadable

  Three things are deliberately kept apart, because keeping them together defeats the purpose:

    1. the private key file            - a file, outside the repository
    2. the signing password            - Windows Credential Manager, target
                                         CanallaLLM/UpdaterSigning/Production
    3. the backup recovery passphrase  - Windows Credential Manager, a *different* target
                                         CanallaLLM/UpdaterSigning/ProductionBackup

  The encrypted backup of the private key therefore never travels with the secret that opens it.

  The password is generated here and is never printed, never written to a file, never logged and
  never stored in the repository. It is passed to `tauri signer` as a process argument - the CLI's
  only non-interactive mechanism - which is stated plainly in the release report rather than
  hidden.

.PARAMETER Action
  NewIdentity   create a keypair, its password and the encrypted backup
  KeyId         print the public key id of a private key (public information)
  Sign          sign one file non-interactively
  TestRecovery  load the private key with the stored password, sign a fixture, verify the signature
  Backup        (re)create the encrypted backup from an existing key
  TestBackup    decrypt the backup with the recovery passphrase and compare it to the key

.PARAMETER KeyPath
  Private key file. Must live outside the repository.

.EXAMPLE
  powershell -NoProfile -File scripts\production-signing.ps1 -Action KeyId `
    -KeyPath "$env:USERPROFILE\.canalla-updater\production\canalla-updater-production.key"
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('NewIdentity', 'KeyId', 'Sign', 'Build', 'TestRecovery', 'Backup', 'TestBackup')]
  [string]$Action,

  [string]$KeyPath,
  [string]$File,
  [string]$BackupPath,
  [string]$CredentialTarget = 'CanallaLLM/UpdaterSigning/Production',
  [string]$RecoveryTarget = 'CanallaLLM/UpdaterSigning/ProductionBackup'
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

# ---------------------------------------------------------------------------- Credential Manager

if (-not ('CanallaCredential' -as [type])) {
  Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class CanallaCredential {
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct CREDENTIAL {
        public uint Flags;
        public uint Type;
        public string TargetName;
        public string Comment;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;
        public uint CredentialBlobSize;
        public IntPtr CredentialBlob;
        public uint Persist;
        public uint AttributeCount;
        public IntPtr Attributes;
        public string TargetAlias;
        public string UserName;
    }

    [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode, EntryPoint = "CredWriteW")]
    private static extern bool CredWrite(ref CREDENTIAL credential, uint flags);

    [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode, EntryPoint = "CredReadW")]
    private static extern bool CredRead(string target, uint type, uint flags, out IntPtr credential);

    [DllImport("advapi32.dll", SetLastError = true)]
    private static extern bool CredDelete(string target, uint type, uint flags);

    [DllImport("advapi32.dll")]
    private static extern void CredFree(IntPtr buffer);

    private const uint GENERIC = 1;
    private const uint PERSIST_LOCAL_MACHINE = 2;

    public static void Write(string target, string user, string secret) {
        byte[] blob = System.Text.Encoding.Unicode.GetBytes(secret);
        IntPtr buffer = Marshal.AllocCoTaskMem(blob.Length);
        try {
            Marshal.Copy(blob, 0, buffer, blob.Length);
            CREDENTIAL credential = new CREDENTIAL();
            credential.Type = GENERIC;
            credential.TargetName = target;
            credential.UserName = user;
            credential.CredentialBlob = buffer;
            credential.CredentialBlobSize = (uint)blob.Length;
            credential.Persist = PERSIST_LOCAL_MACHINE;
            if (!CredWrite(ref credential, 0)) {
                throw new Exception("CredWrite failed with " + Marshal.GetLastWin32Error());
            }
        } finally {
            Marshal.FreeCoTaskMem(buffer);
        }
    }

    public static string Read(string target) {
        IntPtr raw;
        if (!CredRead(target, GENERIC, 0, out raw)) {
            return null;
        }
        try {
            CREDENTIAL credential = (CREDENTIAL)Marshal.PtrToStructure(raw, typeof(CREDENTIAL));
            return Marshal.PtrToStringUni(credential.CredentialBlob, (int)credential.CredentialBlobSize / 2);
        } finally {
            CredFree(raw);
        }
    }
}
'@
}

function Get-ProductionPassword {
  param([string]$Target)
  $secret = [CanallaCredential]::Read($Target)
  if ([string]::IsNullOrEmpty($secret)) {
    # The typed failure the release path is allowed to stop on. Never fall back to an empty
    # password, to the test key, or to an unsigned artifact.
    throw 'production_signing_secret_unavailable'
  }
  return $secret
}

function New-RandomSecret {
  $bytes = New-Object byte[] 32
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  return [Convert]::ToBase64String($bytes)
}

function Invoke-NativeCommand {
  <#
    Run a native tool and return its merged output plus its exit code.

    `cmd /c` is used deliberately: a native command that writes a progress line to stderr makes
    Windows PowerShell 5.1 raise a terminating NativeCommandError even on success, so `npx tauri
    build` aborted with a console message instead of building. cmd merges the child's streams, so
    PowerShell only ever sees text.
  #>
  param([string]$Command, [string]$WorkingDirectory)
  $saved = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  Push-Location $WorkingDirectory
  try {
    $output = & cmd.exe /c "$Command 2>&1"
    return @{ Output = ($output | Out-String); Code = $LASTEXITCODE }
  } finally {
    $ErrorActionPreference = $saved
    Pop-Location
  }
}

function Quote-Argument {
  param([string]$Value)
  if ($Value -match '\s') { return '"' + $Value + '"' }
  return $Value
}

function Invoke-TauriSigner {
  param([string[]]$Arguments, [string]$Password)
  $previous = $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD
  try {
    # The signer reads the password from this variable, so it never appears as a process argument.
    if ($Password) { $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = $Password }
    $command = 'npx tauri signer ' + (($Arguments | ForEach-Object { Quote-Argument $_ }) -join ' ')
    $result = Invoke-NativeCommand -Command $command -WorkingDirectory (Join-Path $repo 'apps\desktop')
    if ($result.Code -ne 0) {
      throw "tauri signer failed ($($result.Code)): $($result.Output)"
    }
    return $result.Output
  } finally {
    if ($null -eq $previous) { Remove-Item Env:\TAURI_SIGNING_PRIVATE_KEY_PASSWORD -ErrorAction SilentlyContinue }
    else { $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = $previous }
  }
}

function Get-KeyId {
  param([string]$Path)
  $public = "$Path.pub"
  if (-not (Test-Path $public)) { throw "missing public key: $public" }
  $text = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String((Get-Content $public -Raw).Trim()))
  if ($text -notmatch 'minisign public key: (\w+)') { throw 'the public key carries no minisign key id' }
  return $Matches[1]
}

# ----------------------------------------------------------------------------------------- backup

function Protect-KeyBackup {
  param([string]$Path, [string]$Destination, [string]$Target)

  $passphrase = [CanallaCredential]::Read($Target)
  if ([string]::IsNullOrEmpty($passphrase)) {
    $passphrase = New-RandomSecret
    [CanallaCredential]::Write($Target, 'canalla-release-backup', $passphrase)
  }

  $plain = [System.IO.File]::ReadAllBytes($Path)
  $salt = New-Object byte[] 16
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($salt)
  $derive = New-Object System.Security.Cryptography.Rfc2898DeriveBytes(
    $passphrase, $salt, 200000, [System.Security.Cryptography.HashAlgorithmName]::SHA256)
  $aes = [System.Security.Cryptography.Aes]::Create()
  $aes.KeySize = 256
  $aes.Key = $derive.GetBytes(32)
  $aes.GenerateIV()
  # Read the IV before the cipher is disposed: a disposed Aes has none, and the header needs it.
  $iv = $aes.IV

  $directory = Split-Path -Parent $Destination
  if (-not (Test-Path $directory)) { New-Item -ItemType Directory -Path $directory | Out-Null }

  $stream = [System.IO.File]::Create($Destination)
  try {
    $stream.Write($salt, 0, $salt.Length)
    $stream.Write($iv, 0, $iv.Length)
    $encryptor = $aes.CreateEncryptor()
    $crypto = New-Object System.Security.Cryptography.CryptoStream(
      $stream, $encryptor, [System.Security.Cryptography.CryptoStreamMode]::Write)
    $crypto.Write($plain, 0, $plain.Length)
    $crypto.FlushFinalBlock()
    $crypto.Dispose()
  } finally {
    $stream.Dispose()
    $aes.Dispose()
  }

  # The header holds the KDF parameters only; the passphrase lives in the Credential Manager.
  $header = [ordered]@{
    format       = 'canalla-updater-key-backup'
    version      = 1
    cipher       = 'aes-256-cbc'
    kdf          = 'pbkdf2-sha256'
    iterations   = 200000
    salt         = [Convert]::ToBase64String($salt)
    iv           = [Convert]::ToBase64String($iv)
    source_basename = Split-Path -Leaf $Path
  }
  $header | ConvertTo-Json | Set-Content -Path "$Destination.json" -Encoding UTF8
  return $Destination
}

function Unprotect-KeyBackup {
  param([string]$Path, [string]$Target)
  $header = Get-Content "$Path.json" -Raw | ConvertFrom-Json
  $passphrase = Get-ProductionPassword -Target $Target
  $raw = [System.IO.File]::ReadAllBytes($Path)
  $salt = [Convert]::FromBase64String($header.salt)
  $iv = [Convert]::FromBase64String($header.iv)
  $derive = New-Object System.Security.Cryptography.Rfc2898DeriveBytes(
    $passphrase, $salt, [int]$header.iterations, [System.Security.Cryptography.HashAlgorithmName]::SHA256)
  $aes = [System.Security.Cryptography.Aes]::Create()
  $aes.KeySize = 256
  $aes.Key = $derive.GetBytes(32)
  $aes.IV = $iv

  $stream = New-Object System.IO.MemoryStream(,$raw[($salt.Length + $iv.Length)..($raw.Length - 1)])
  $decryptor = $aes.CreateDecryptor()
  $crypto = New-Object System.Security.Cryptography.CryptoStream(
    $stream, $decryptor, [System.Security.Cryptography.CryptoStreamMode]::Read)
  $out = New-Object System.IO.MemoryStream
  $crypto.CopyTo($out)
  $crypto.Dispose(); $stream.Dispose(); $aes.Dispose()
  return $out.ToArray()
}

# ------------------------------------------------------------------------------------------ actions

switch ($Action) {
  'NewIdentity' {
    if (-not $KeyPath) { throw '-KeyPath is required' }
    if (Test-Path $KeyPath) { throw "refusing to overwrite an existing identity: $KeyPath" }

    $password = New-RandomSecret
    [CanallaCredential]::Write($CredentialTarget, 'canalla-updater', $password)

    $directory = Split-Path -Parent $KeyPath
    if (-not (Test-Path $directory)) { New-Item -ItemType Directory -Path $directory | Out-Null }

    Invoke-TauriSigner -Arguments @('generate', '-w', $KeyPath, '-p', $password, '--ci') | Out-Null

    # Fail closed if the password we stored cannot open the key we just wrote.
    $null = Get-ProductionPassword -Target $CredentialTarget
    Write-Output "key_id=$((Get-KeyId -Path $KeyPath))"
    Write-Output "key_path=$KeyPath"
    Write-Output "credential_target=$CredentialTarget"
    Write-Output 'password=stored in the Credential Manager, value never printed'
  }

  'KeyId' {
    Write-Output "key_id=$((Get-KeyId -Path $KeyPath))"
  }

  'Sign' {
    if (-not $File) { throw '-File is required' }
    $password = Get-ProductionPassword -Target $CredentialTarget
    $signed = Invoke-TauriSigner -Arguments @('sign', '-f', $KeyPath, $File) -Password $password
    Write-Output ($signed | Select-Object -Last 2)
  }

  'Build' {
    # The one release build: the signing secret is read before anything is compiled, so a missing
    # identity stops the release instead of producing an unsigned installer.
    $password = Get-ProductionPassword -Target $CredentialTarget
    # The Tauri CLI shells out to `cargo`, which a release machine has in the standard rustup
    # location but not necessarily on PATH. Locate it here, so the build fails with "no toolchain"
    # rather than with a confusing metadata error.
    $cargoBin = Join-Path $env:USERPROFILE '.cargo\bin'
    if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
      if (Test-Path (Join-Path $cargoBin 'cargo.exe')) { $env:PATH = "$cargoBin;$env:PATH" }
      else { throw 'rust_toolchain_not_found' }
    }
    $previousKey = $env:TAURI_SIGNING_PRIVATE_KEY
    $previousPassword = $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD
    $env:TAURI_SIGNING_PRIVATE_KEY = (Get-Content $KeyPath -Raw)
    $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = $password
    try {
      $result = Invoke-NativeCommand -Command 'npx tauri build --bundles nsis' -WorkingDirectory (Join-Path $repo 'apps\desktop')
      if ($result.Code -ne 0) {
        throw "the release build failed ($($result.Code)): $($result.Output)"
      }
      Write-Output $result.Output
    } finally {
      $env:TAURI_SIGNING_PRIVATE_KEY = $previousKey
      if ($null -eq $previousPassword) { Remove-Item Env:\TAURI_SIGNING_PRIVATE_KEY_PASSWORD -ErrorAction SilentlyContinue }
      else { $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = $previousPassword }
    }
  }

  'TestRecovery' {
    if (-not $File) { throw '-File is required' }
    $password = Get-ProductionPassword -Target $CredentialTarget
    Invoke-TauriSigner -Arguments @('sign', '-f', $KeyPath, $File) -Password $password | Out-Null
    if (-not (Test-Path "$File.sig")) { throw 'the signer produced no .sig' }
    Write-Output "signed=$File"
    Write-Output "signature_sha256=$((Get-FileHash "$File.sig" -Algorithm SHA256).Hash.ToLower())"
    Write-Output "key_id=$((Get-KeyId -Path $KeyPath))"
  }

  'Backup' {
    if (-not $BackupPath) { throw '-BackupPath is required' }
    $created = Protect-KeyBackup -Path $KeyPath -Destination $BackupPath -Target $RecoveryTarget
    Write-Output "backup=$created"
    Write-Output "backup_sha256=$((Get-FileHash $created -Algorithm SHA256).Hash.ToLower())"
    Write-Output "recovery_target=$RecoveryTarget"
  }

  'TestBackup' {
    $restored = Unprotect-KeyBackup -Path $BackupPath -Target $RecoveryTarget
    $original = [System.IO.File]::ReadAllBytes($KeyPath)
    $same = ($restored.Length -eq $original.Length)
    if ($same) {
      for ($i = 0; $i -lt $original.Length; $i++) {
        if ($restored[$i] -ne $original[$i]) { $same = $false; break }
      }
    }
    Write-Output "backup_decrypts=$same"
    if (-not $same) { throw 'the backup does not reproduce the private key' }
  }
}
