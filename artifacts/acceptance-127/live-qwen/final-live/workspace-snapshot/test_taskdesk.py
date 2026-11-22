"""Automated tests for the TaskDesk application."""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, init_db, DB_PATH


@pytest.fixture
def client(tmp_path):
    """Create a test client with a fresh in-memory-style DB."""
    db_file = str(tmp_path / "test_taskdesk.db")
    init_db(db_file)
    app.config["TESTING"] = True
    # Monkey-patch DB_PATH for test isolation
    import app as app_module
    old_path = app_module.DB_PATH
    app_module.DB_PATH = db_file
    with app.test_client() as c:
        yield c
    app_module.DB_PATH = old_path


class TestCreateTask:
    def test_create_task(self, client):
        res = client.post("/api/tasks", json={"title": "My first task"})
        assert res.status_code == 201
        data = res.get_json()
        assert data["title"] == "My first task"
        assert data["status"] == "todo"
        assert data["id"] is not None

    def test_create_task_with_description(self, client):
        res = client.post("/api/tasks", json={"title": "T", "description": "D"})
        assert res.status_code == 201
        assert res.get_json()["description"] == "D"

    def test_create_task_missing_title(self, client):
        res = client.post("/api/tasks", json={})
        assert res.status_code == 400
        assert "error" in res.get_json()

    def test_create_task_empty_title(self, client):
        res = client.post("/api/tasks", json={"title": "   "})
        assert res.status_code == 400


class TestListTasks:
    def test_list_empty(self, client):
        res = client.get("/api/tasks")
        assert res.status_code == 200
        assert res.get_json() == []

    def test_list_returns_created(self, client):
        client.post("/api/tasks", json={"title": "Alpha"})
        client.post("/api/tasks", json={"title": "Beta"})
        res = client.get("/api/tasks")
        tasks = res.get_json()
        assert len(tasks) == 2
        titles = {t["title"] for t in tasks}
        assert titles == {"Alpha", "Beta"}


class TestSearch:
    def test_search_case_insensitive(self, client):
        client.post("/api/tasks", json={"title": "Hello World"})
        res = client.get("/api/tasks?q=hello")
        assert len(res.get_json()) == 1
        res = client.get("/api/tasks?q=HELLO")
        assert len(res.get_json()) == 1
        res = client.get("/api/tasks?q=world")
        assert len(res.get_json()) == 1

    def test_search_no_match(self, client):
        client.post("/api/tasks", json={"title": "Gamma"})
        res = client.get("/api/tasks?q=zzz")
        assert res.get_json() == []


class TestUpdateStatus:
    def test_update_status(self, client):
        r = client.post("/api/tasks", json={"title": "S"})
        tid = r.get_json()["id"]
        res = client.patch(f"/api/tasks/{tid}", json={"status": "done"})
        assert res.status_code == 200
        assert res.get_json()["status"] == "done"

    def test_update_invalid_status(self, client):
        r = client.post("/api/tasks", json={"title": "S2"})
        tid = r.get_json()["id"]
        res = client.patch(f"/api/tasks/{tid}", json={"status": "bogus"})
        assert res.status_code == 400

    def test_update_not_found(self, client):
        res = client.patch("/api/tasks/99999", json={"status": "done"})
        assert res.status_code == 404


class TestDelete:
    def test_delete_task(self, client):
        r = client.post("/api/tasks", json={"title": "Del"})
        tid = r.get_json()["id"]
        res = client.delete(f"/api/tasks/{tid}")
        assert res.status_code == 200
        assert client.get("/api/tasks").get_json() == []

    def test_delete_not_found(self, client):
        res = client.delete("/api/tasks/99999")
        assert res.status_code == 404


class TestWebUI:
    def test_index_page(self, client):
        res = client.get("/")
        assert res.status_code == 200
        assert b"TaskDesk" in res.data
