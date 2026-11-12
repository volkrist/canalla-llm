# A plain, visible keep-alive around the coordinator.
#
# The coordinator is the state machine: it is safe to start again at any moment because it keeps
# its state on disk, reconciles a session it did not finish, and never opens a second Pod. This
# wrapper exists only because a detached Python process on Windows can disappear without a
# traceback (no stderr, no exit record) and a 48-hour window must not silently stop waiting.
#
# It is not a service and not a scheduled task: one user process, one window, `cancel` still
# stops everything, and the loop exits when the coordinator finishes or the owner cancels.
$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$python = Join-Path $repo 'apps\backend\.venv\Scripts\python.exe'
$coordinator = Join-Path $repo 'artifacts\acceptance-126\coordinator.py'
$state = Join-Path $repo 'artifacts\acceptance-126\coordinator\state.json'
$cancel = Join-Path $repo 'artifacts\acceptance-126\coordinator\cancel.flag'
$log = Join-Path $repo 'artifacts\acceptance-126\coordinator\wrapper.log'

$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'

function Note([string]$message) {
    $line = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ') + ' ' + $message
    Add-Content -LiteralPath $log -Value $line -Encoding utf8
}

# The owner's 2026-11-12 grant: the cycle cap is $1.45 and at most six Pods in total (five
# already exist), so exactly one more start is possible. The coordinator takes both from its CLI,
# so the grant lives here and cannot drift from the state it guards.
$budget = '1.45'
$maxPods = '6'

Note 'wrapper started'
while ($true) {
    if (Test-Path -LiteralPath $cancel) { Note 'cancel flag present; wrapper stops'; break }
    if (Test-Path -LiteralPath $state) {
        try {
            $payload = Get-Content -LiteralPath $state -Raw | ConvertFrom-Json
            if ($payload.finished -eq $true) { Note ('coordinator finished: ' + $payload.outcome); break }
        } catch { Note 'state.json is not readable yet; continuing' }
    }
    Note 'starting the coordinator'
    & $python $coordinator monitor --budget-usd $budget --max-pods $maxPods
    $code = $LASTEXITCODE
    Note ('coordinator exited with ' + $code)
    Start-Sleep -Seconds 30
}
Note 'wrapper stopped'
