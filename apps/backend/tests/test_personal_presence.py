import asyncio
from datetime import timedelta

import pytest
from fastapi import WebSocketDisconnect
from sqlalchemy import select

from app.config import get_settings
from app.context_builder import ContextBuilder, MemoryCandidate, MemoryExtractor
from app.database import SessionLocal
from app.main import app
from app.models import Chat, Message, User, now
from app.presence import PresenceManager


def make_memory(client, headers, **values):
    r = client.post("/memory", headers=headers, json={"content": "FastAPI backend", **values})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.parametrize(
    "category", ["identity", "preference", "project", "decision", "fact", "instruction", "other"]
)
def test_memory_crud_category(client, auth, category):
    headers = auth()
    m = make_memory(client, headers, category=category)
    assert client.get("/memory", headers=headers).json()[0]["id"] == m["id"]
    r = client.patch(
        "/memory/" + m["id"],
        headers=headers,
        json={"content": "Изменено", "is_pinned": True, "is_active": False},
    )
    assert r.status_code == 200 and r.json()["is_pinned"] and not r.json()["is_active"]
    assert client.get("/memory?q=Изменено&category=" + category, headers=headers).json()
    assert client.delete("/memory/" + m["id"], headers=headers).status_code == 200
    assert client.get("/memory", headers=headers).json() == []


@pytest.mark.parametrize(
    "body",
    [
        {"content": ""},
        {"content": "x" * 3001},
        {"content": "ok", "importance": 6},
        {"content": "ok", "category": "bad"},
        {"content": "ok", "user_id": "other"},
    ],
)
def test_memory_limits(client, auth, body):
    assert client.post("/memory", headers=auth(), json=body).status_code == 422


def test_personal_ownership_and_sources(client, auth):
    a, b = auth(), auth("bob@example.com")
    chat = client.post("/chats", headers=a, json={}).json()["id"]
    client.post("/chats/" + chat + "/stream", headers=a, json={"content": "hello"})
    message = client.get("/chats/" + chat + "/messages", headers=a).json()[0]["id"]
    project = client.post("/projects", headers=a, json={"name": "Alex LLM"}).json()["id"]
    for source in ({"source_chat_id": chat}, {"source_message_id": message}, {"project_id": project}):
        assert client.post("/memory", headers=b, json={"content": "attack", **source}).status_code == 404
    m = make_memory(client, a, source_message_id=message, project_id=project)
    assert m["source_chat_id"] == chat
    assert client.patch("/memory/" + m["id"], headers=b, json={"is_pinned": True}).status_code == 404
    assert client.delete("/memory/" + m["id"], headers=b).status_code == 404
    assert client.get("/memory", headers=b).json() == []
    assert client.get("/messages/" + message + "/memory", headers=b).status_code == 404
    assert client.patch("/projects/" + project, headers=b, json={"name": "attack"}).status_code == 404
    other_chat = client.post("/chats", headers=b, json={}).json()["id"]
    assert client.patch("/chats/" + other_chat, headers=b, json={"project_id": project}).status_code == 404
    assert (
        client.patch("/chats/" + chat, headers=a, json={"project_id": project}).json()["project_id"]
        == project
    )
    assert client.patch("/chats/" + chat, headers=a, json={"project_id": None}).json()["project_id"] is None
    assert client.get("/chats/" + chat + "/context-preview", headers=b).status_code == 404
    assert (
        client.patch(
            "/projects/" + project, headers=a, json={"name": "Alex", "status": "archived"}
        ).status_code
        == 200
    )
    assert client.patch("/chats/" + chat, headers=a, json={"project_id": project}).status_code == 422


def test_profile_and_settings(client, auth):
    a = auth()
    profile = client.get("/profile", headers=a).json()
    assert profile["use_memory"] and profile["relevant_memory"]
    body = {"display_name": "Alex", "custom_instructions": "Кратко по-русски", "max_memories": 8}
    assert client.patch("/profile", headers=a, json=body).json()["display_name"] == "Alex"
    assert client.patch("/profile", headers=a, json={**body, "max_memories": 100}).status_code == 422
    assert (
        client.patch("/profile", headers=a, json={**body, "custom_instructions": "x" * 2001}).status_code
        == 422
    )


def test_context_priority_budgets_and_usage(client, auth):
    a, b = auth(), auth("bob@example.com")
    project = client.post(
        "/projects", headers=a, json={"name": "Alex LLM", "description": "Tauri FastAPI"}
    ).json()["id"]
    chat = client.post("/chats", headers=a, json={}).json()["id"]
    client.patch("/chats/" + chat, headers=a, json={"project_id": project})
    pinned = make_memory(client, a, content="Отвечать по-русски", is_pinned=True)
    matched = make_memory(client, a, project_id=project)
    relevant = make_memory(client, a, content="FastAPI endpoints use JWT")
    make_memory(client, a, content="Любит корейскую кухню", importance=5)
    disabled = make_memory(client, a, content="FastAPI disabled", is_active=False, is_pinned=True)
    deleted = make_memory(client, a, content="FastAPI deleted", is_pinned=True)
    client.delete("/memory/" + deleted["id"], headers=a)
    make_memory(client, b, content="FastAPI private Bob", is_pinned=True)
    client.patch(
        "/profile", headers=a, json={"display_name": "Alex", "custom_instructions": "Отвечай кратко"}
    )
    preview = client.get("/chats/" + chat + "/context-preview?prompt=FastAPI", headers=a).json()
    assert preview["memory_ids"] == [pinned["id"], matched["id"], relevant["id"]]
    assert disabled["id"] not in preview["memory_ids"]
    assert preview["messages"][0]["role"] == "system"
    assert sum(m["content"] == "FastAPI" for m in preview["messages"]) == 1
    assert "Отвечай кратко" in preview["messages"][1]["content"]
    assert all(m["use_count"] == 0 for m in client.get("/memory", headers=a).json())
    client.post("/chats/" + chat + "/stream", headers=a, json={"content": "FastAPI"})
    rows = client.get("/chats/" + chat + "/messages", headers=a).json()
    used = client.get("/messages/" + rows[-1]["id"] + "/memory", headers=a).json()
    assert len(used) == 3 and all(m["use_count"] == 1 and m["last_used_at"] for m in used)
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "alice@example.com"))
        c = db.get(Chat, chat)
        current = Message(id="current", chat_id=chat, role="user", content="FastAPI unique", created_at=now())
        settings = get_settings().model_copy(
            update={"memory_max_chars": 20, "memory_max_items": 1, "context_history_chars": 10}
        )
        result = ContextBuilder(settings).build(db, user, c, current)
        assert len(result["memories"]) <= 1
        assert sum(len(m["content"]) for m in result["memories"]) <= 20
        assert result["recent_message_count"] == 0
        user.use_memory = False
        assert ContextBuilder().build(db, user, c, current)["memory_ids"] == []


def test_context_history_trim_and_exact_current(client, auth):
    a = auth()
    chat = client.post("/chats", headers=a, json={}).json()["id"]
    client.post("/chats/" + chat + "/stream", headers=a, json={"content": "first"})
    client.post("/chats/" + chat + "/stream", headers=a, json={"content": "second"})
    preview = client.get("/chats/" + chat + "/context-preview?prompt=third", headers=a).json()
    assert preview["recent_message_count"] == 4
    assert preview["messages"][-1]["content"] == "third"
    assert sum(m["content"] == "third" for m in preview["messages"]) == 1


def test_ticket_one_use_ttl_invalid_and_auth(client, auth):
    assert client.post("/presence/ws-ticket").status_code == 401
    a = auth()
    manager = app.state.presence
    ticket = client.post("/presence/ws-ticket", headers=a).json()["ticket"]
    assert "." not in ticket
    assert manager.consume(ticket)
    assert manager.consume(ticket) is None
    assert manager.consume("invalid") is None
    ticket = client.post("/presence/ws-ticket", headers=a).json()["ticket"]
    manager.clock = lambda: now() + timedelta(seconds=61)
    assert manager.consume(ticket) is None
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/presence?ticket=invalid"):
            pass


def test_ws_heartbeat_multiple_connections_and_privacy(client, auth):
    a = auth()

    def grant():
        return client.post("/presence/ws-ticket", headers=a).json()["ticket"]

    t = grant()
    with client.websocket_connect("/ws/presence?ticket=" + t) as ws:
        snapshot = ws.receive_json()
        assert snapshot["type"] == "snapshot"
        row = snapshot["users"][0]
        assert row["status"] == "online"
        assert set(row) == {"key", "display_name", "status", "using_ai", "last_seen"}
        assert "alice@example.com" not in str(snapshot)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/presence?ticket=" + t):
                pass
        with client.websocket_connect("/ws/presence?ticket=" + grant()) as second:
            assert second.receive_json()["users"][0]["status"] == "online"
        ws.send_json({"type": "heartbeat"})
        while ws.receive_json()["type"] != "ack":
            pass
        assert client.get("/presence", headers=a).json()[0]["status"] == "online"
    with client.websocket_connect("/ws/presence?ticket=" + grant()) as ws:
        assert ws.receive_json()["type"] == "snapshot"


def test_idle_offline_restart_and_multiple_sessions(client, auth):
    a = auth()
    uid = client.get("/auth/me", headers=a).json()["id"]
    time = now()
    manager = PresenceManager(
        get_settings().model_copy(update={"presence_idle_seconds": 30, "presence_offline_seconds": 75}),
        clock=lambda: time,
    )
    first = manager.connect(uid)
    second = manager.connect(uid)
    time += timedelta(seconds=35)
    manager.touch(first)
    assert manager.snapshot()[0]["status"] == "idle"
    manager.touch(first, True)
    assert manager.snapshot()[0]["status"] == "online"
    manager.disconnect(first)
    time += timedelta(seconds=60)
    manager.touch(second)
    assert manager.snapshot()[0]["status"] == "idle"
    manager.reconcile()
    assert manager.snapshot()[0]["status"] == "offline"
    manager.connect(uid)
    time += timedelta(seconds=76)
    assert manager.snapshot()[0]["status"] == "offline"


def test_using_ai_counter_and_error_cleanup(client, auth):
    a = auth()
    uid = client.get("/auth/me", headers=a).json()["id"]
    chat = client.post("/chats", headers=a, json={}).json()["id"]

    second_chat = client.post("/chats", headers=a, json={}).json()["id"]

    async def count_scenario():
        one = await app.state.compute.begin_generation(uid, chat, "mock")
        two = await app.state.compute.begin_generation(uid, second_chat, "mock")
        assert app.state.presence.snapshot()[0]["using_ai"]
        app.state.compute.finish_generation(one, "error")
        assert app.state.presence.snapshot()[0]["using_ai"]
        app.state.compute.finish_generation(two, "stopped")
        assert not app.state.presence.snapshot()[0]["using_ai"]

    asyncio.run(count_scenario())
    original = app.state.provider

    class Broken:
        async def stream_with_usage(self, *args):
            assert app.state.presence.snapshot()[0]["using_ai"]
            yield "partial"
            raise RuntimeError("private")

    app.state.provider = Broken()
    try:
        r = client.post("/chats/" + chat + "/stream", headers=a, json={"content": "test"})
        assert "private" not in r.text
        assert not app.state.presence.snapshot()[0]["using_ai"]
    finally:
        app.state.provider = original


def test_capture_interface_has_no_automatic_side_effects():
    class TestExtractor(MemoryExtractor):
        async def extract(self, *args):
            return [MemoryCandidate("Test only")]

    assert asyncio.run(TestExtractor().extract("a", "b", []))[0].content == "Test only"


def test_context_reaches_mock_provider_once_and_settings_apply(client, auth):
    from app.providers import MockLLMProvider

    a = auth()
    chat = client.post("/chats", headers=a, json={}).json()["id"]
    make_memory(client, a, content="Всегда отвечать кратко", is_pinned=True)
    foreign_project = client.post("/projects", headers=a, json={"name": "Other project"}).json()["id"]
    make_memory(client, a, content="Unrelated pinned secret", is_pinned=True, project_id=foreign_project)
    seen = []

    class CaptureMock(MockLLMProvider):
        async def stream_chat(self, messages):
            seen.extend(messages)
            async for token in super().stream_chat(messages):
                yield token

    original = app.state.provider
    app.state.provider = CaptureMock(0)
    try:
        assert (
            client.post(
                "/chats/" + chat + "/stream", headers=a, json={"content": "unique current prompt"}
            ).status_code
            == 200
        )
        assert sum(m["content"] == "unique current prompt" for m in seen) == 1
        assert any("Всегда отвечать кратко" in m["content"] for m in seen)
        assert not any("Unrelated pinned secret" in m["content"] for m in seen)
        assert seen[0]["role"] == "system"
        client.patch("/profile", headers=a, json={"display_name": "Alex", "use_memory": False})
        assert client.get("/chats/" + chat + "/context-preview", headers=a).json()["memories"] == []
    finally:
        app.state.provider = original


def test_generation_context_snapshot_and_ttft(client, auth):
    headers = auth()
    project = client.post("/projects", headers=headers, json={"name": "Original project"}).json()
    memory = client.post(
        "/memory",
        headers=headers,
        json={"content": "Original memory", "category": "fact", "importance": 5, "is_pinned": True},
    ).json()
    chat = client.post("/chats", headers=headers, json={}).json()
    client.patch("/chats/" + chat["id"], headers=headers, json={"project_id": project["id"]})
    client.post("/chats/" + chat["id"] + "/stream", headers=headers, json={"content": "Test snapshot"})
    messages = client.get("/chats/" + chat["id"] + "/messages", headers=headers).json()
    answer = messages[-1]
    assert answer["generation_started_at"] and answer["first_token_at"] and answer["completed_at"]
    assert answer["ttft_ms"] >= 0
    assert answer["cancellation"]["provider_stream_closed"] is True
    assert answer["cancellation"]["upstream_cancel_confirmed"] is None
    path = "/messages/" + answer["id"] + "/context"
    before = client.get(path, headers=headers).json()
    assert before["project"] == "Original project"
    assert before["memory_count"] == 1
    assert before["memory_chars"] == len("Original memory")
    assert before["current_prompt_chars"] == len("Test snapshot")
    client.patch("/projects/" + project["id"], headers=headers, json={"name": "Renamed project"})
    client.delete("/memory/" + memory["id"], headers=headers)
    after = client.get(path, headers=headers).json()
    assert after["project"] == before["project"]
    assert after["memory_count"] == 1
    assert after["total_chars"] == before["total_chars"]
    assert client.get(path, headers=auth("snapshot-other@example.com")).status_code == 404
