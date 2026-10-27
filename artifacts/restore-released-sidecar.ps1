$ErrorActionPreference = 'Stop'
# Restore the sidecar that came with the published 1.2.5 installer. A locally built fix must not
# stay installed: the installed binaries have to match the released ones, and no unreleased code
# may run on the owner's machine.
$install = 'C:\Users\Volkr\AppData\Local\Programs\Canalla LLM'
$backup = 'C:\Users\Volkr\Documents\Codex\2026-09-13\x20\outputs\alex-llm\artifacts\pre-sidecar-replace-20261028T072342Z'

Get-Process -Name 'alex-llm' -ErrorAction SilentlyContinue | ForEach-Object { $_.CloseMainWindow() | Out-Null }
Start-Sleep -Seconds 5
Get-Process -Name 'alex-llm' -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.Id -Force }
foreach ($p in @(Get-Process -Name 'alex-backend' -ErrorAction SilentlyContinue)) {
  try { Stop-Process -Id $p.Id -Force } catch { }
}
Start-Sleep -Seconds 2

Remove-Item -Recurse -Force (Join-Path $install 'sidecar\alex-backend')
Copy-Item -Recurse -Force (Join-Path $backup 'alex-backend') (Join-Path $install 'sidecar\alex-backend')
Copy-Item -Force (Join-Path $backup 'sidecar-alex-host-loop.exe') (Join-Path $install 'sidecar\alex-host-loop.exe')
Copy-Item -Force (Join-Path $backup 'root-alex-host-loop.exe') (Join-Path $install 'alex-host-loop.exe')

Start-Process -FilePath (Join-Path $install 'alex-llm.exe')
for ($i = 0; $i -lt 90; $i++) {
  try {
    $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/health' -UseBasicParsing -TimeoutSec 3
    Write-Output $r.Content
    break
  } catch { Start-Sleep -Seconds 1 }
}
$exe = Join-Path $install 'sidecar\alex-backend\alex-backend.exe'
Write-Output ('restored_sha256=' + (Get-FileHash $exe -Algorithm SHA256).Hash.ToLowerInvariant())
Get-Content (Join-Path $install 'sidecar\alex-backend\build-stamp.json')
