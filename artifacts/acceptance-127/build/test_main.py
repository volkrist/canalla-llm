
from fastapi.testclient import TestClient

import main


def test_search_is_case_insensitive(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB_PATH", str(tmp_path / "tasks.db"))
    main.init_db()
    client = TestClient(main.app)
    created = client.post("/tasks", json={"title": "Alpha task"})
    assert created.status_code == 201
    assert client.get("/tasks", params={"q": "alpha"}).json()[0]["title"] == "Alpha task"


def test_create_and_status(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB_PATH", str(tmp_path / "tasks.db"))
    main.init_db()
    client = TestClient(main.app)
    task_id = client.post("/tasks", json={"title": "Beta"}).json()["id"]
    assert client.patch(f"/tasks/{task_id}", json={"title": "Beta", "status": "done"}).json()["status"] == "done"
    assert client.get("/tasks", params={"status": "done"}).json()[0]["status"] == "done"
