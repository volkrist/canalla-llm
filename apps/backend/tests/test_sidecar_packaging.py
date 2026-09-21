import logging
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from app.documents.embedding import EmbeddingUnavailable, LocalEmbeddingProvider
from app.packaging import alembic_config, app_resource_root, apply_runtime_environment, package_file
from app.product import RUNTIME_PROTOCOL_VERSION, VERSION
from app.runtime_log import RedactTicket, configure_backend_logging

BACKEND = Path(__file__).resolve().parents[1]


def test_resource_root_ignores_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ALEX_APP_RESOURCE_DIR", raising=False)
    root = app_resource_root()
    assert (root / "alembic.ini").is_file()
    assert (root / "alembic").is_dir()
    assert tmp_path not in root.parents and root != tmp_path


def test_alembic_script_location_is_absolute(monkeypatch):
    monkeypatch.setenv("ALEX_APP_RESOURCE_DIR", str(BACKEND))
    config = alembic_config()
    location = Path(config.get_main_option("script_location"))
    assert location.is_absolute()
    assert location.is_dir()
    assert (location / "versions").is_dir()


def test_remote_runtime_resource_exists():
    path = package_file("app", "compute", "remote_runtime.py")
    assert b"ALEX_LLM_PHASE" in path.read_bytes()


def test_packaged_env_uses_tauri_cors(monkeypatch):
    monkeypatch.setenv("ALEX_PACKAGED", "1")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_CONNECTION_MODE", raising=False)
    apply_runtime_environment()
    assert os.environ["APP_ENV"] == "production"
    assert os.environ["LLM_PROVIDER"] == "llamacpp"
    assert os.environ["LLM_CONNECTION_MODE"] == "runpod"
    assert "tauri.localhost" in os.environ["CORS_ORIGINS"]
    assert "http://localhost:1420" not in os.environ["CORS_ORIGINS"]


def test_fresh_and_upgrade_migrate_via_resolver(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    env = {
        **os.environ,
        "ALEX_APP_RESOURCE_DIR": str(BACKEND),
        "JWT_SECRET": "test-only-secret-not-for-deployment-" + "a" * 32,
        "PYTHONPATH": str(BACKEND),
    }
    fresh = tmp_path / "fresh.db"
    env["DATABASE_URL"] = "sqlite:///" + fresh.as_posix()
    subprocess.run(
        [sys.executable, "-c", "from app.runtime_entry import migrate; migrate()"],
        cwd=elsewhere,
        env=env,
        check=True,
        capture_output=True,
    )
    with sqlite3.connect(fresh) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == ("0014",)

    older = tmp_path / "older.db"
    env["DATABASE_URL"] = "sqlite:///" + older.as_posix()
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0001"],
        cwd=BACKEND,
        env=env,
        check=True,
        capture_output=True,
    )
    with sqlite3.connect(older) as db:
        db.execute(
            "INSERT INTO users (id,email,password_hash,created_at) VALUES ('u','keep@example.com','hash','2026-09-14')"
        )
        db.commit()
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == ("0001",)
    subprocess.run(
        [sys.executable, "-c", "from app.runtime_entry import migrate; migrate()"],
        cwd=elsewhere,
        env=env,
        check=True,
        capture_output=True,
    )
    with sqlite3.connect(older) as db:
        assert db.execute("SELECT email FROM users").fetchone() == ("keep@example.com",)
        assert db.execute("SELECT is_owner FROM users").fetchone() == (1,)
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == ("0014",)
        assert older.exists()


def test_migrate_failure_preserves_db(tmp_path):
    db = tmp_path / "keep.db"
    db.write_text("not a database", encoding="utf-8")
    env = {
        **os.environ,
        "ALEX_APP_RESOURCE_DIR": str(BACKEND),
        "DATABASE_URL": "sqlite:///" + db.as_posix(),
        "JWT_SECRET": "test-only-secret-not-for-deployment-" + "a" * 32,
        "PYTHONPATH": str(BACKEND),
    }
    result = subprocess.run(
        [sys.executable, "-c", "from app.runtime_entry import migrate; migrate()"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
    )
    assert result.returncode != 0
    assert db.read_text(encoding="utf-8") == "not a database"


def test_backend_log_redacts_secrets_and_records_identity(tmp_path, monkeypatch):
    secret = "super-secret-jwt-value-0123456789abcdef"
    monkeypatch.setenv("JWT_SECRET", secret)
    monkeypatch.setenv("RUNPOD_API_KEY", "rp-live-key-should-hide")
    path = tmp_path / "backend.log"
    configure_backend_logging(path, port=8018, mode="packaged")
    logging.getLogger("alex.runtime").info("jwt=%s", secret)
    logging.getLogger("alex.runtime").info("runpod=%s", "rp-live-key-should-hide")
    logging.getLogger("alex.runtime").info("Authorization: Bearer abc.def.ghi")
    text = path.read_text(encoding="utf-8")
    assert secret not in text
    assert "rp-live-key-should-hide" not in text
    assert "[redacted]" in text
    assert VERSION in text
    assert str(RUNTIME_PROTOCOL_VERSION) in text
    assert "packaged" in text
    assert "8018" in text
    ticket = RedactTicket()
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "Authorization: Bearer abc", (), None)
    ticket.filter(record)
    assert "Bearer abc" not in str(record.msg)
    assert "[redacted]" in str(record.msg)


def test_embeddings_absent_are_unavailable(tmp_path):
    provider = LocalEmbeddingProvider(verified_root=tmp_path)
    try:
        provider.load()
        raise AssertionError("embeddings must not pretend to be ready")
    except EmbeddingUnavailable:
        pass


def test_playwright_is_not_imported_at_module_level():
    import ast

    tree = ast.parse((BACKEND / "app" / "tools" / "tinyfish" / "browser.py").read_text(encoding="utf-8"))
    names = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module.split(".")[0])
    assert "playwright" not in names
