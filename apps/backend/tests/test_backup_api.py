"""Typed backup API: authentication, create/list/verify/diagnose, and no HTTP restore."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from app.backup.archive import BackupService
from app.main import app


def database_url_path() -> Path:
    url = os.environ["DATABASE_URL"]
    return Path(url.split("sqlite:///", 1)[1])


@pytest.fixture
def backup_root(tmp_path):
    """A data root whose live database is a consistent copy of the test database."""
    root = tmp_path / "api-root"
    (root / "data").mkdir(parents=True)
    (root / "documents").mkdir(parents=True)
    (root / "documents" / ("a" * 32)).write_bytes(b"document")
    source = sqlite3.connect(database_url_path())
    destination = sqlite3.connect(root / "data" / "alex.db")
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    service = BackupService(root)
    app.state.backup = service
    yield root
    app.state.backup = None


def test_backup_api_requires_authentication(client, backup_root):
    assert client.get("/backup").status_code == 401
    assert client.post("/backup", json={"label": "x"}).status_code == 401
    assert client.get("/backup/diagnostic").status_code == 401


def test_backup_api_creates_lists_and_verifies(client, auth, backup_root):
    headers = auth()
    created = client.post("/backup", json={"label": "перед обновлением"}, headers=headers)
    assert created.status_code == 200, created.text
    payload = created.json()
    assert payload["verified"] is True
    assert payload["documents"] == 1
    backup_id = payload["id"]

    listed = client.get("/backup", headers=headers)
    assert listed.status_code == 200
    body = listed.json()
    assert [row["id"] for row in body["backups"]] == [backup_id]
    assert body["backups"][0]["verified"] is True
    assert body["keep"] == {"automatic": 3, "manual": 10}
    assert body["state"]["busy"] == ""

    verified = client.post("/backup/verify", json={"backup_id": backup_id}, headers=headers)
    assert verified.status_code == 200
    assert verified.json()["verified"] is True

    # The audit trail lives in the database and holds no path outside the backups root.
    with sqlite3.connect(database_url_path()) as db:
        rows = db.execute("SELECT kind, status, backup_id FROM backup_events ORDER BY created_at").fetchall()
    assert ("manual", "created", backup_id) in rows
    assert ("verify", "verified", backup_id) in rows


def test_backup_api_refuses_a_traversal_id(client, auth, backup_root):
    headers = auth()
    for evil in ("../secrets", "..", "C:/Windows", "nested/child", ""):
        response = client.post("/backup/verify", json={"backup_id": evil}, headers=headers)
        assert response.status_code in (400, 422), evil
    assert not (backup_root / "backups" / ".." / "secrets").exists()


def test_backup_api_has_no_restore_route(client, auth, backup_root):
    """Restore must never be reachable over HTTP: it runs while the backend is stopped."""
    routes = {getattr(route, "path", "") for route in app.routes}
    assert not any(path.startswith("/backup/restore") for path in routes)
    headers = auth()
    assert client.post("/backup/restore", json={"backup_id": "x"}, headers=headers).status_code in (404, 405)


def test_backup_api_reports_a_corrupt_backup_without_touching_it(client, auth, backup_root):
    headers = auth()
    broken = backup_root / "backups" / "20260101T000000Z-manual"
    broken.mkdir(parents=True)
    (broken / "manifest.json").write_text("{not json", encoding="utf-8")

    listed = client.get("/backup", headers=headers).json()
    row = listed["backups"][0]
    assert row["complete"] is False
    assert row["problem"] == "backup_corrupt"

    response = client.post("/backup/verify", json={"backup_id": row["id"]}, headers=headers)
    assert response.status_code == 400
    assert response.json()["detail"]
    with sqlite3.connect(database_url_path()) as db:
        assert db.execute("SELECT COUNT(*) FROM backup_events WHERE status='failed'").fetchone()[0] == 1
    assert broken.is_dir(), "a rejected backup is never deleted by the API"


def test_backup_api_is_busy_safe(client, auth, backup_root):
    headers = auth()
    service = app.state.backup
    service._busy = "restoring"
    try:
        response = client.post("/backup", json={}, headers=headers)
        assert response.status_code == 409
        assert client.post("/backup/verify", json={"backup_id": "x"}, headers=headers).status_code == 409
    finally:
        service._busy = ""


def test_backup_diagnostic_is_supportable_and_secret_free(client, auth, backup_root):
    headers = auth()
    client.post("/backup", json={"label": ""}, headers=headers)
    response = client.get("/backup/diagnostic", headers=headers)
    assert response.status_code == 200
    body = response.json()
    for field in (
        "app_version",
        "backup_format_version",
        "schema_revision",
        "head_revision",
        "migration_pending",
        "backups_root",
        "backups_count",
        "verified_count",
        "last_verified_at",
        "disk_free_bytes",
    ):
        assert field in body, field
    text = response.text.lower()
    for forbidden in ("jwt", "secret", "token", "runpod", "password"):
        assert forbidden not in text, forbidden
