$ErrorActionPreference = 'Stop'
Push-Location (Join-Path (Split-Path -Parent $PSScriptRoot) 'apps\backend')
try {
    & .\.venv\Scripts\python.exe -m alembic upgrade head
    & .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
} finally { Pop-Location }
