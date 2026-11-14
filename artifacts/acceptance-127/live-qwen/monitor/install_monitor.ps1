# Register the persistent Canalla GPU monitor with Windows Task Scheduler.
#
# One task, one-shot per run, every 10 minutes, indefinitely. It depends on no editor window and no
# resident process: Task Scheduler starts `monitor.py --once`, which takes a lock, checks the
# provider, writes its state and exits. `IgnoreNew` plus the file lock means two passes can never
# overlap, and therefore can never create a second Pod.
#
# Run (no administrator required for a task that runs as you):
#   powershell -NoProfile -ExecutionPolicy Bypass -File artifacts\acceptance-127\live-qwen\monitor\install_monitor.ps1
# Remove:
#   powershell -NoProfile -ExecutionPolicy Bypass -File ...\install_monitor.ps1 -Uninstall

param(
  [string]$Python = "",
  [string]$TaskName = "Canalla LLM GPU Monitor",
  [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'

$repo = (& git -C $PSScriptRoot rev-parse --show-toplevel).Trim()
if (-not $repo) { throw "Cannot resolve the repository root from $PSScriptRoot" }
$monitor = Join-Path $repo 'artifacts\acceptance-127\live-qwen\monitor\monitor.py'
if (-not (Test-Path $monitor)) { throw "monitor.py not found at $monitor" }
if (-not $Python) { $Python = Join-Path $repo 'apps\backend\.venv\Scripts\python.exe' }
if (-not (Test-Path $Python)) { throw "Python interpreter not found at $Python" }

if ($Uninstall) {
  $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "UNINSTALLED $TaskName"
  } else {
    Write-Output "NOT_FOUND $TaskName"
  }
  return
}

$runtime = if ($env:CANALLA_MONITOR_DIR) { $env:CANALLA_MONITOR_DIR } else { Join-Path $env:LOCALAPPDATA 'CanallaMonitor' }
New-Item -ItemType Directory -Force -Path $runtime | Out-Null

$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$escape = { param($text) [System.Security.SecurityElement]::Escape([string]$text) }
$arguments = '"' + $monitor + '" --pass'
$start = (Get-Date).AddMinutes(1).ToString('yyyy-MM-ddTHH:mm:ss')

$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Canalla LLM 1.2.7 - one pass per minute, each pass performs two read-only checks 30 seconds apart, so the effective GPU cadence is 30 seconds. Both regional volumes are reported. Starts the prepared LIVE acceptance only when a stored owner authorization exists.</Description>
    <URI>\$($escape.Invoke($TaskName))</URI>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <StartBoundary>$start</StartBoundary>
      <Enabled>true</Enabled>
      <Repetition>
        <Interval>PT1M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>$($escape.Invoke($identity))</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>$($escape.Invoke($Python))</Command>
      <Arguments>$($escape.Invoke($arguments))</Arguments>
      <WorkingDirectory>$($escape.Invoke($repo))</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

$registered = Register-ScheduledTask -TaskName $TaskName -Xml $xml -Force
$info = Get-ScheduledTaskInfo -TaskName $TaskName

$marker = [ordered]@{
  task_name     = $TaskName
  registered_at = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
  python        = $Python
  monitor       = $monitor
  repo          = $repo
  runtime_dir   = $runtime
  interval      = 'PT1M'
  check_gap     = 30
  check_count   = 2
  logon_type    = 'InteractiveToken'
}
$marker | ConvertTo-Json | ForEach-Object {
  [System.IO.File]::WriteAllText((Join-Path $runtime 'scheduler.json'), $_, (New-Object System.Text.UTF8Encoding($false)))
}

Write-Output "INSTALLED $TaskName"
Write-Output "STATE $registered.State"
Write-Output "NEXT_RUN $($info.NextRunTime)"
Write-Output "RUNTIME $runtime"
