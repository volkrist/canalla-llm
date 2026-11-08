# Suspend or resume one Canalla process, which is where the native Computer host loop lives.
#
# The sidecar is a separate process, so Qwen keeps streaming while the backend stops seeing the
# host's heartbeats and job claims. Reversible, no kill, no re-pair.
#
# Safety rules:
# * a specific `-ProcessId` is required; this script never suspends "every alex-llm.exe" and can
#   therefore never freeze the operator's real session together with an acceptance instance;
# * `-ExpectedStart` pins the process identity, so a recycled PID is never resumed;
# * `-WatchdogSeconds` starts an independent process that resumes the same instance even if the
#   harness crashes. A suspended test instance must never be left frozen.
param(
    [switch]$Suspend,
    [switch]$Resume,
    [int]$ProcessId = 0,
    [string]$ExpectedStart = '',
    [int]$WatchdogSeconds = 0
)

$ErrorActionPreference = 'Stop'

function Get-Identity([System.Diagnostics.Process]$Process) {
    return $Process.StartTime.ToUniversalTime().ToString('o')
}

if ($ProcessId -le 0) {
    Write-Output 'process_id_required'
    exit 2
}

$target = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
if (-not $target) {
    Write-Output ("missing=" + $ProcessId)
    exit 3
}
if ($ExpectedStart -and (Get-Identity $target) -ne $ExpectedStart) {
    Write-Output 'identity_mismatch'
    exit 4
}

Add-Type -Namespace Win32 -Name Ntdll -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("ntdll.dll")]
public static extern int NtSuspendProcess(System.IntPtr handle);
[System.Runtime.InteropServices.DllImport("ntdll.dll")]
public static extern int NtResumeProcess(System.IntPtr handle);
'@ -ErrorAction SilentlyContinue

if ($Suspend) {
    [void][Win32.Ntdll]::NtSuspendProcess($target.Handle)
    $start = Get-Identity $target
    if ($WatchdogSeconds -gt 0) {
        $watchdog = Join-Path $PSScriptRoot 'resume-watchdog.ps1'
        Start-Process -FilePath 'powershell' -WindowStyle Hidden -ArgumentList @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $watchdog,
            '-ProcessId', $ProcessId, '-ExpectedStart', $start, '-Seconds', $WatchdogSeconds
        )
    }
    Write-Output ("suspended=" + $ProcessId + " watchdog=" + $WatchdogSeconds)
} elseif ($Resume) {
    [void][Win32.Ntdll]::NtResumeProcess($target.Handle)
    Write-Output ("resumed=" + $ProcessId)
} else {
    Write-Output 'nothing_to_do'
    exit 5
}
