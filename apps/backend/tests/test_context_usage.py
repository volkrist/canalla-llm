"""Context meter: the estimate the composer reads must match the model context.

The meter is a UI feature with a truth requirement: it reports the parts that really
enter the prompt (system, profile, memories, project, documents, history, draft), the
window the product serves the model with (``llm_context_window`` = llama.cpp
``--ctx-size``) and nothing else. No GPU, no secret, no memory-counter side effect.
"""

from app import context_usage as usage
from app.compute.models import GenerationUsage
from app.config import get_settings
from app.context_builder import ContextBuilder
from app.database import SessionLocal
from app.models import Chat, Memory, Message, User, now

CHAT = {"content": "hello"}
PART_KEYS = set(usage.LABELS)


def make_chat(client, headers):
    created = client.post("/chats", headers=headers, json={})
    assert created.status_code == 201, created.text
    return created.json()["id"]


def snapshot(client, headers, chat, prompt=""):
    response = client.get(f"/chats/{chat}/context-usage", headers=headers, params={"prompt": prompt})
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- estimate


def test_estimate_tokens_is_deterministic_and_script_aware():
    assert usage.estimate_tokens("") == 0
    assert usage.estimate_tokens(None) == 0
    # Four latin characters per token, two cyrillic ones: russian text is denser.
    assert usage.estimate_tokens("abcd") == 1
    assert usage.estimate_tokens("abcdefgh") == 2
    assert usage.estimate_tokens("абвг") == 2
    assert usage.estimate_tokens("абвгабвг") == 4
    # One message body never costs less than one token once it has content.
    assert usage.estimate_tokens("a") == 1


def test_estimate_is_proportional_and_monotonic():
    short = usage.estimate_tokens("Привет, как дела?")
    long = usage.estimate_tokens("Привет, как дела? " * 20)
    assert 0 < short < long
    assert long >= short * 15


def test_summarize_reports_the_window_it_was_given():
    parts = [usage.part(usage.PART_SYSTEM, usage.LABELS[usage.PART_SYSTEM], "x" * 400)]
    report = usage.summarize(parts, limit_tokens=1000, model="m")
    assert report["limit_tokens"] == 1000
    assert report["used_tokens"] == parts[0]["tokens"]
    assert report["remaining_tokens"] == 1000 - report["used_tokens"]
    assert report["percent"] == round(report["used_tokens"] / 10, 1)
    assert report["estimated"] is True and report["method"] == "chars_per_token"


def test_summarize_never_hides_an_overflow():
    # A draft longer than the window must not be clamped into a reassuring number.
    parts = [usage.part(usage.PART_DRAFT, usage.LABELS[usage.PART_DRAFT], "y" * 40000)]
    report = usage.summarize(parts, limit_tokens=1000, model="m")
    assert report["remaining_tokens"] == 0
    assert report["percent"] > 100


# ----------------------------------------------------------------------------- route


def test_context_usage_reports_the_served_window(client, auth):
    headers = auth()
    chat = make_chat(client, headers)
    body = snapshot(client, headers, chat)

    settings = get_settings()
    assert body["limit_tokens"] == settings.llm_context_window == 32768
    assert body["model"] == settings.llm_model
    assert body["estimated"] is True
    assert body["used_tokens"] > 0  # the system prompt is always present
    assert body["percent"] == round(body["used_tokens"] / body["limit_tokens"] * 100, 1)
    keys = {part["key"] for part in body["parts"]}
    assert usage.PART_SYSTEM in keys
    assert keys <= PART_KEYS
    assert body["measured"] is None


def test_context_usage_counts_the_draft_and_the_history(client, auth):
    headers = auth()
    chat = make_chat(client, headers)

    empty = snapshot(client, headers, chat)
    draft = snapshot(client, headers, chat, "Расскажи про Aurora backups")
    assert draft["used_tokens"] > empty["used_tokens"]
    draft_part = next(part for part in draft["parts"] if part["key"] == usage.PART_DRAFT)
    assert draft_part["tokens"] > 0
    assert draft_part["chars"] == len("Расскажи про Aurora backups")

    client.post(f"/chats/{chat}/stream", headers=headers, json=CHAT)
    after = snapshot(client, headers, chat, "Ещё вопрос")
    history = next(part for part in after["parts"] if part["key"] == usage.PART_HISTORY)
    assert history["tokens"] > 0 and history["chars"] > 0
    assert after["used_tokens"] > draft["used_tokens"]


def test_context_usage_counts_memory_and_profile_once_they_exist(client, auth):
    headers = auth()
    chat = make_chat(client, headers)
    before = snapshot(client, headers, chat)

    created = client.post("/memory", headers=headers, json={"content": "Живу в Сеуле", "is_pinned": True})
    assert created.status_code == 201
    after = snapshot(client, headers, chat, "Где я живу?")
    keys = {part["key"] for part in after["parts"]}
    assert usage.PART_PINNED in keys or usage.PART_MEMORY in keys
    assert after["used_tokens"] > before["used_tokens"]


def test_context_usage_sums_to_the_parts_it_reports(client, auth):
    headers = auth()
    chat = make_chat(client, headers)
    client.post(f"/chats/{chat}/stream", headers=headers, json=CHAT)
    body = snapshot(client, headers, chat, "Продолжим")
    assert body["used_tokens"] == sum(part["tokens"] for part in body["parts"])
    assert all(part["chars"] > 0 for part in body["parts"])


def test_context_usage_is_ownership_scoped(client, auth):
    a, b = auth(), auth("bob@example.com")
    chat = make_chat(client, a)
    assert client.get(f"/chats/{chat}/context-usage", headers=b).status_code == 404
    assert client.get("/chats/does-not-exist/context-usage", headers=a).status_code == 404


def test_context_usage_does_not_touch_memory_counters(client, auth):
    headers = auth()
    chat = make_chat(client, headers)
    created = client.post(
        "/memory", headers=headers, json={"content": "Живу в Сеуле", "is_pinned": True}
    ).json()
    for _ in range(3):
        snapshot(client, headers, chat, "Где я живу?")
    with SessionLocal() as db:
        row = db.get(Memory, created["id"])
        assert row.use_count == 0 and row.last_used_at is None


def test_context_usage_returns_the_measured_prompt_size_when_known(client, auth):
    headers = auth()
    chat = make_chat(client, headers)
    assert snapshot(client, headers, chat)["measured"] is None

    with SessionLocal() as db:
        user = db.query(User).first()
        row = Message(id="seed-measured", chat_id=chat, role="user", content="seed", created_at=now())
        db.add(row)
        db.flush()
        db.add(
            GenerationUsage(
                id="usage-1",
                user_id=user.id,
                chat_id=chat,
                message_id=row.id,
                provider="mock",
                status="completed",
                input_tokens=11800,
            )
        )
        db.commit()

    measured = snapshot(client, headers, chat)["measured"]
    assert measured["prompt_tokens"] == 11800
    assert measured["at"]


def test_context_usage_payload_has_no_secret_or_internal_fields(client, auth):
    headers = auth()
    chat = make_chat(client, headers)
    body = snapshot(client, headers, chat)
    assert set(body) == {
        "model",
        "limit_tokens",
        "used_tokens",
        "remaining_tokens",
        "percent",
        "estimated",
        "method",
        "parts",
        "measured",
    }
    for part in body["parts"]:
        assert set(part) == {"key", "label", "chars", "tokens"}
    assert "JWT" not in str(body) and "secret" not in str(body).lower()


def test_preview_carries_the_same_usage_block(client, auth):
    headers = auth()
    chat = make_chat(client, headers)
    preview = client.get(
        f"/chats/{chat}/context-preview", headers=headers, params={"prompt": "Aurora"}
    ).json()
    assert preview["context_usage"]["limit_tokens"] == 32768
    assert preview["context_usage"]["used_tokens"] > 0


# --------------------------------------------------------------------------- builder


def test_the_window_comes_from_settings_not_from_the_frontend(client, auth):
    headers = auth()
    chat = make_chat(client, headers)
    settings = get_settings().model_copy(update={"llm_context_window": 4096})

    with SessionLocal() as db:
        user = db.query(User).first()
        row = db.get(Chat, chat)
        current = Message(id="preview", chat_id=chat, role="user", content="привет", created_at=now())
        built = ContextBuilder(settings).build(db, user, row, current)

    assert built["context_usage"]["limit_tokens"] == 4096
    assert built["context_usage"]["parts"]
    # The messages sent to the model are unchanged by the accounting.
    assert built["messages"][0]["role"] == "system"
    assert built["messages"][-1] == {"role": "user", "content": "привет"}


def test_build_still_reports_its_original_metadata(client, auth):
    headers = auth()
    chat = make_chat(client, headers)
    with SessionLocal() as db:
        user = db.query(User).first()
        row = db.get(Chat, chat)
        current = Message(id="preview", chat_id=chat, role="user", content="привет", created_at=now())
        built = ContextBuilder().build(db, user, row, current)
    for key in ("messages", "memory_ids", "sources", "budgets", "total_chars", "rag_warning"):
        assert key in built
    assert built["total_chars"] == sum(len(m["content"]) for m in built["messages"])
