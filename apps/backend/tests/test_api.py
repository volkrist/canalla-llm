import asyncio
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.database import SessionLocal
from app.main import app
from app.models import Message, User
from app.product import VERSION
from app.providers import MockLLMProvider
from tests.db_helpers import require_scalar
from tests.settings_factory import make_settings


def new_chat(client, headers):
    response = client.post("/chats", headers=headers, json={})
    assert response.status_code == 201
    return response.json()["id"]


def test_auth_hash_login_and_expiration(client, auth):
    headers = auth("Alice@example.com")
    me = client.get("/auth/me", headers=headers)
    assert me.status_code == 200 and me.json()["email"] == "alice@example.com"
    assert "password_hash" not in me.json()
    with SessionLocal() as db:
        user = require_scalar(db, select(User))
        assert user.password_hash.startswith("$argon2id$")
        user_id = user.id
    assert (
        client.post(
            "/auth/login", json={"email": "ALICE@example.com", "password": "test-password-123"}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/auth/login", json={"email": "alice@example.com", "password": "wrong-password"}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/auth/login", json={"email": "missing@example.com", "password": "wrong-password"}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/auth/register", json={"email": "alice@example.com", "password": "test-password-123"}
        ).status_code
        == 409
    )
    token = jwt.encode(
        {
            "sub": user_id,
            "iat": datetime.now(timezone.utc) - timedelta(hours=2),
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
            "iss": "alex-llm",
            "aud": "alex-desktop",
        },
        get_settings().jwt_secret,
        algorithm="HS256",
    )
    assert client.get("/auth/me", headers={"Authorization": "Bearer " + token}).status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Bearer invalid"}).status_code == 401


@pytest.mark.parametrize("path", ["/chats", "/chats/unknown", "/chats/unknown/messages", "/auth/me"])
def test_auth_required(client, path):
    assert client.get(path).status_code == 401


def test_user_isolation_all_routes(client, auth):
    alice, bob = auth(), auth("bob@example.com")
    chat_id = new_chat(client, alice)
    assert (
        client.post(
            f"/chats/{chat_id}/messages", headers=alice, json={"content": "Secret message"}
        ).status_code
        == 201
    )
    assert client.get("/chats", headers=bob).json() == []
    for suffix in ["", "/messages", "/generation"]:
        assert client.get(f"/chats/{chat_id}{suffix}", headers=bob).status_code == 404
    assert client.delete(f"/chats/{chat_id}", headers=bob).status_code == 404
    for suffix in ["/messages", "/stream"]:
        assert (
            client.post(f"/chats/{chat_id}{suffix}", headers=bob, json={"content": "Attack"}).status_code
            == 404
        )
    assert client.get(f"/chats/{chat_id}/messages", headers=alice).json()[0]["content"] == "Secret message"


def test_stream_persists_order_title_and_cascade(client, auth):
    headers = auth()
    chat_id = new_chat(client, headers)
    response = client.post(f"/chats/{chat_id}/stream", headers=headers, json={"content": "Привет 🌍"})
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert (
        "event: meta" in response.text and "event: delta" in response.text and "event: done" in response.text
    )
    rows = client.get(f"/chats/{chat_id}/messages", headers=headers).json()
    assert [row["role"] for row in rows] == ["user", "assistant"]
    assert "Привет 🌍" in rows[1]["content"] and "```python" in rows[1]["content"]
    assert client.get(f"/chats/{chat_id}", headers=headers).json()["title"] == "Привет 🌍"
    assert client.get(f"/chats/{chat_id}/messages?limit=1&offset=1", headers=headers).json() == rows[1:]
    assert client.delete(f"/chats/{chat_id}", headers=headers).status_code == 204
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Message)) == 0


def test_validation_and_role_injection(client, auth):
    headers = auth()
    chat_id = new_chat(client, headers)
    for body in [{"content": " "}, {"content": "x" * 32001}, {"content": "bad", "role": "assistant"}]:
        assert client.post(f"/chats/{chat_id}/messages", headers=headers, json=body).status_code == 422
    assert client.post("/auth/register", json={"email": "invalid", "password": "short"}).status_code == 422


def test_cors(client):
    response = client.options(
        "/chats", headers={"Origin": "http://127.0.0.1:1420", "Access-Control-Request-Method": "POST"}
    )
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:1420"
    response = client.options(
        "/chats", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"}
    )
    assert response.status_code == 400 and "access-control-allow-origin" not in response.headers


def test_production_config_rejects_insecure_defaults():
    with pytest.raises(ValueError):
        make_settings(_env_file=None, app_env="production", jwt_secret="x" * 64, cors_origins=["*"])
    with pytest.raises(ValueError):
        make_settings(
            _env_file=None, app_env="production", jwt_secret="x" * 64, cors_origins=["http://example.com"]
        )


def test_busy_chat_blocks_mutations(client, auth):
    headers = auth()
    chat_id = new_chat(client, headers)
    app.state.generating.add(chat_id)
    assert client.delete(f"/chats/{chat_id}", headers=headers).status_code == 409
    for suffix in ["messages", "stream"]:
        assert (
            client.post(f"/chats/{chat_id}/{suffix}", headers=headers, json={"content": "test"}).status_code
            == 409
        )
    assert client.get(f"/chats/{chat_id}", headers=headers).status_code == 200


def test_provider_failure_preserves_partial_output(client, auth):
    class BrokenProvider(MockLLMProvider):
        async def stream_chat(self, messages):
            yield "partial response"
            raise RuntimeError("private-upstream-url-and-secret")

    previous = app.state.provider
    app.state.provider = BrokenProvider()
    try:
        headers = auth()
        chat_id = new_chat(client, headers)
        response = client.post(f"/chats/{chat_id}/stream", headers=headers, json={"content": "hello"})
        assert "event: error" in response.text
        assert "private-upstream" not in response.text and "event: done" not in response.text
        rows = client.get(f"/chats/{chat_id}/messages", headers=headers).json()
        assert rows[-1]["content"] == "partial response"
        assert chat_id not in app.state.generating
    finally:
        app.state.provider = previous


def test_health_and_mock_chat(client):
    assert client.get("/health").json() == {
        "status": "ok",
        "provider": "mock",
        "llm_ready": True,
        "product": "alex-llm",
        "version": VERSION,
        "runtime_protocol_version": 1,
        "instance": None,
    }
    assert "demo" in asyncio.run(MockLLMProvider(0).chat([{"role": "user", "content": "demo"}]))
