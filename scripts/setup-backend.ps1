$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location (Join-Path $projectRoot 'apps\backend')
try {
    if (!(Test-Path '.venv\Scripts\python.exe')) {
        py -3.12 -m venv .venv
        if ($LASTEXITCODE) { throw 'Python 3.12 venv creation failed' }
    }
    & .\.venv\Scripts\python.exe -m pip install -r requirements.lock
    if ($LASTEXITCODE) { throw 'Dependency installation failed' }
    & .\.venv\Scripts\python.exe -m pip install --no-deps -e .
    if ($LASTEXITCODE) { throw 'Backend installation failed' }
    if (!(Test-Path '.env')) {
        $example = Get-Content (Join-Path $projectRoot '.env.example') -Raw
        $randomBytes = New-Object byte[] 48
        [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($randomBytes)
        $secret = [Convert]::ToBase64String($randomBytes)
        $example.Replace('replace-with-a-random-secret-at-least-32-characters', $secret) | Set-Content '.env' -Encoding utf8
        Write-Host 'Created backend .env with a random JWT secret.'
    }
    & .\.venv\Scripts\python.exe -m alembic upgrade head
    if ($LASTEXITCODE) { throw 'Database migration failed' }
} finally { Pop-Location }
