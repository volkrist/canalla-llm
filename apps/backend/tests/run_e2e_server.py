"""Run an isolated database-backed mock API for Playwright; never uses the developer DB."""

import os
import secrets
import subprocess
import tempfile
from pathlib import Path

import uvicorn

if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="alex-e2e-") as directory:
        os.environ.update(
            DATABASE_URL="sqlite:///" + (Path(directory) / "e2e.db").as_posix(),
            JWT_SECRET=secrets.token_urlsafe(48),
            APP_ENV="test",
            CORS_ORIGINS='["http://127.0.0.1:1421"]',
            LLM_PROVIDER="mock",
            MOCK_DELAY="0.025",
            RUNPOD_API_KEY="",
            COMPUTE_BACKGROUND_ENABLED="false",
            ADMIN_EMAILS="[]",
            ALLOW_USER_COMPUTE_START="false",
            DOCUMENT_STORAGE_DIR=str(Path(directory) / "documents"),
        )
        import sys

        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
        uvicorn.run("app.main:app", host="127.0.0.1", port=8001)
