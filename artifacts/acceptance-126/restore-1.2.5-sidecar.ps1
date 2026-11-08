$ErrorActionPreference = 'Stop'
$install = 'C:\Users\Volkr\AppData\Local\Programs\Canalla LLM'
$backup = 'C:\Users\Volkr\Documents\Codex\2026-09-13\x20\outputs\alex-llm\artifacts\pre-sidecar-replace-20261108T214156Z\alex-backend'
Get-Process -Name 'alex-llm' -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.Id -Force }
Start-Sleep -Seconds 4
$target = Join-Path $install 'sidecar\alex-backend'
if (Test-Path $target) { Remove-Item -Recurse -Force $target }
Copy-Item -Recurse -Force $backup $target
$hash = (Get-FileHash (Join-Path $target 'alex-backend.exe') -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Output ('restored_sha256=' + $hash)
Start-Process -FilePath (Join-Path $install 'alex-llm.exe')
for ($i = 0; $i -lt 60; $i++) {
  try {
    $health = (Invoke-WebRequest -Uri 'http://127.0.0.1:8000/health' -UseBasicParsing -TimeoutSec 3).Content
    Write-Output $health
    break
  } catch { Start-Sleep -Seconds 1 }
}
