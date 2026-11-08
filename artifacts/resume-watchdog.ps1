# Independent safety net for a suspended acceptance instance.
#
# `suspend-app.ps1 -Suspend -WatchdogSeconds N` starts this script detached. If the harness dies,
# throws or is killed, this process still resumes the SAME instance after N seconds. It never
# touches a different process: the PID is checked together with its start time.
param(
    [int]$ProcessId = 0,
    [string]$ExpectedStart = '',
    [int]$Seconds = 90
)

Start-Sleep -Seconds $Seconds
$target = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
if (-not $target) { exit 0 }
$start = $target.StartTime.ToUniversalTime().ToString('o')
if ($ExpectedStart -and $start -ne $ExpectedStart) { exit 0 }
& (Join-Path $PSScriptRoot 'suspend-app.ps1') -Resume -ProcessId $ProcessId -ExpectedStart $ExpectedStart
exit $LASTEXITCODE
