"""API tests for TaskDesk using Flask test client."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from taskdesk import create_app
import pytest


@pytest.fixture
def client(tmp_path):
    app = create_app(db_path=str(tmp_path / "test.db"))
    app.testing = True
    return app.test_client()


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_create_and_list(client):
    r = client.post("/api/tasks", json={"title": "Learn Flask", "description": "x"})
    assert r.status_code == 201
    t = r.get_json()["task"]
    assert t["title"] == "Learn Flask"
    assert t["status"] == "todo"
    r2 = client.get("/api/tasks")
    titles = [x["title"] for x in r2.get_json()["tasks"]]
    assert "Learn Flask" in titles


def test_create_requires_title(client):
    r = client.post("/api/tasks", json={"title": "   "})
    assert r.status_code == 400


def test_invalid_status(client):
    r = client.post("/api/tasks", json={"title": "x", "status": "nope"})
    assert r.status_code == 400


def test_search_case_insensitive(client):
    client.post("/api/tasks", json={"title": "Fix Bug Alpha"})
    client.post("/api/tasks", json={"title": "unrelated task"})
    r = client.get("/api/tasks?q=fix bug")
    tasks = r.get_json()["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["title"] == "Fix Bug Alpha"


def test_status_change(client):
    r = client.post("/api/tasks", json={"title": "Move"})
    tid = r.get_json()["task"]["id"]
    r2 = client.patch(f"/api/tasks/{tid}", json={"status": "done"})
    assert r2.status_code == 200
    assert r2.get_json()["task"]["status"] == "done"


def test_status_change_invalid(client):
    r = client.post("/api/tasks", json={"title": "Move"})
    tid = r.get_json()["task"]["id"]
    r2 = client.patch(f"/api/tasks/{tid}", json={"status": "bad"})
    assert r2.status_code == 400


def test_get_and_not_found(client):
    r = client.post("/api/tasks", json={"title": "Z"})
    tid = r.get_json()["task"]["id"]
    assert client.get(f"/api/tasks/{tid}").status_code == 200
    assert client.get("/api/tasks/999999").status_code == 404


def test_delete(client):
    r = client.post("/api/tasks", json={"title": "Del"})
    tid = r.get_json()["task"]["id"]
    assert client.delete(f"/api/tasks/{tid}").status_code == 200
    assert client.get(f"/api/tasks/{tid}").status_code == 404


def test_filter_by_status(client):
    a = client.post("/api/tasks", json={"title": "A"})
    b = client.post("/api/tasks", json={"title": "B"})
    client.patch(f"/api/tasks/{a.get_json()['task']['id']}", json={"status": "done"})
    r = client.get("/api/tasks?status=done")
    assert [t["title"] for t in r.get_json()["tasks"]] == ["A"]
    _ = b


def test_persistence_across_clients(tmp_path):
    dbfile = str(tmp_path / "persist.db")
    c1 = create_app(db_path=dbfile).test_client()
    c1.post("/api/tasks", json={"title": "Persisted"})
    # New app instance, same DB file
    c2 = create_app(db_path=dbfile).test_client()
    tasks = c2.get("/api/tasks").get_json()["tasks"]
    assert any(t["title"] == "Persisted" for t in tasks)


def test_web_ui_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "TaskDesk" in body
    assert "/api/tasks" in body
