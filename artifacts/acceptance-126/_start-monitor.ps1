$ErrorActionPreference = 'Stop'
$script = Join-Path (Split-Path -Parent $PSScriptRoot) 'acceptance-126\run-monitor.ps1'
if (-not (Test-Path -LiteralPath $script)) { throw "missing $script" }
$process = Start-Process powershell -ArgumentList @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $script
) -WindowStyle Minimized -PassThru
"wrapper pid $($process.Id)"
Start-Sleep -Seconds 20
"--- processes ---"
Get-CimInstance Win32_Process -Filter "Name='python.exe' or Name='powershell.exe'" |
    Where-Object { $_.CommandLine -like '*coordinator*' -or $_.CommandLine -like '*run-monitor*' } |
    Select-Object ProcessId, Name, @{n = 'cmd'; e = { $_.CommandLine.Substring(0, [Math]::Min(120, $_.CommandLine.Length)) } } |
    Format-List
