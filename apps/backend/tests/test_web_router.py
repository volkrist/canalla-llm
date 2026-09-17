from app.tools.web_router import canonical_url, classify_web, looks_like_tor, pick_fetch_urls


def test_explicit_auto_phrases_require_search():
    for prompt in (
        "найди в интернете курс доллара",
        "посмотри в интернете погоду",
        "проверь в сети расписание",
        "актуальная информация по Python",
        "What is the latest version?",
        "current news",
        "проверь актуальную версию",
        "проверь прямо сейчас курс",
        "Посмотри актуальную документацию FastAPI",
    ):
        assert classify_web(prompt, "auto").required


def test_auto_greeting_does_not_search():
    assert classify_web("Привет", "auto").required is False
    assert classify_web("hello", "auto").required is False


def test_on_factual_requires_search_chitchat_does_not():
    assert classify_web("Какая актуальная версия Python?", "on").required
    assert classify_web("Привет!", "on").required is False
    assert classify_web("перепиши этот абзац проще", "on").required is False
    assert classify_web("без интернета расскажи анекдот", "on").required is False


def test_off_never_requires_web():
    assert classify_web("latest news", "off").required is False


def test_tor_intent_only_when_user_asks():
    assert looks_like_tor("Через Tor найди официальный onion-ресурс Tor Project.")
    assert looks_like_tor("Open the official .onion site")
    assert looks_like_tor("search via Tor")
    assert looks_like_tor("Поищи это в Tor.")
    assert not looks_like_tor("В этой тестовой папке есть небольшой Python-проект.")
    assert not looks_like_tor("Напиши очень длинный подробный рассказ о числах от 1 до 200.")
    assert not looks_like_tor("Какое verification animal указано в тестовом документе?")


def test_fresh_intent_and_canonical_dedup():
    intent = classify_web("проверь актуальную информацию", "auto")
    assert intent.required and intent.fresh
    assert canonical_url("https://Example.com/docs/?utm_source=x") == "https://example.com/docs"
    urls = pick_fetch_urls(
        [
            {"url": "https://example.com/a", "final_url": "https://example.com/a/", "kind": "search"},
            {"url": "https://example.com/a", "final_url": "https://example.com/a", "kind": "search"},
            {"url": "https://example.com/b", "final_url": "https://example.com/b", "kind": "search"},
        ]
    )
    assert urls == ["https://example.com/a/", "https://example.com/b"]
