# Suspend or resume the packaged Canalla process, which is where the native Computer host loop
# lives. The sidecar is a separate process, so Qwen keeps streaming while the backend stops seeing
# the host's heartbeats and job claims. Reversible, no kill, no re-pair.
param([switch]$Suspend, [switch]$Resume)

$target = Get-Process -Name 'alex-llm' -ErrorAction SilentlyContinue
if (-not $target) {
  Write-Output 'app=not_running'
  exit 2
}

Add-Type -Namespace Win32 -Name Ntdll -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("ntdll.dll")]
public static extern int NtSuspendProcess(System.IntPtr handle);
[System.Runtime.InteropServices.DllImport("ntdll.dll")]
public static extern int NtResumeProcess(System.IntPtr handle);
'@ -ErrorAction SilentlyContinue

foreach ($process in $target) {
  $handle = $process.Handle
  if ($Suspend) {
    [void][Win32.Ntdll]::NtSuspendProcess($handle)
    Write-Output ("suspended=" + $process.Id)
  } elseif ($Resume) {
    [void][Win32.Ntdll]::NtResumeProcess($handle)
    Write-Output ("resumed=" + $process.Id)
  }
}
