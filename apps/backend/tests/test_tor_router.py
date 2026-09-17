from app.tools.policy import WebSettings
from app.tools.tor.router import (
    blocked_link,
    classify_tor,
    extract_page_links,
    looks_like_tor,
    pick_follow_urls,
    pick_tor_fetch_urls,
)


def test_classify_tor_modes():
    off = classify_tor("Через Tor найди onion", "off")
    assert off.allowed is False and off.required is False
    auto = classify_tor("Через Tor найди официальный onion-сервис Tor Project.", "auto")
    assert auto.allowed and auto.required
    denied = classify_tor("не используй Tor", "auto")
    assert denied.allowed is False
    greeting = classify_tor("Привет!", "on")
    assert greeting.allowed is False
    math = classify_tor("2+2", "on")
    assert math.allowed is False
    research = classify_tor("Найди документацию по источникам", "on")
    assert research.allowed is True and research.required is False
    follow = classify_tor("Продолжи поиск по найденным onion-ссылкам и проверь ещё два источника.", "auto")
    assert follow.continue_research and follow.required
    web_on = classify_tor("Какая актуальная версия Python?", "auto")
    assert web_on.allowed is False


def test_looks_like_tor_phrases():
    assert looks_like_tor("search via Tor")
    assert looks_like_tor("hidden service")
    assert looks_like_tor("follow onion links")
    assert looks_like_tor("Поищи это в Tor.")
    assert not looks_like_tor("В этой тестовой папке есть небольшой Python-проект.")


def test_extract_page_links_skips_unsafe_and_downloads():
    html = """
    <a href="/about">About</a>
    <a href="mailto:x@y.z">Mail</a>
    <a href="javascript:alert(1)">JS</a>
    <a href="http://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.onion/app.exe">Exe</a>
    <a href="http://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.onion/docs">Docs</a>
    """
    links = extract_page_links("http://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.onion/", html)
    urls = [item["url"] for item in links]
    assert any(item["is_onion"] and item["same_host"] and "docs" in item["url"] for item in links)
    assert any("/about" in item["url"] for item in links)
    assert not any("mailto" in url for url in urls)
    assert not any(url.endswith(".exe") for url in urls)
    assert blocked_link("http://example.onion/setup.exe")


def test_pick_fetch_and_follow_prefers_official_and_skips_visited():
    official = "http://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.onion/"
    other = "http://bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.onion/"
    sources = [
        {"final_url": other, "authority": "REACHABLE_UNVERIFIED", "kind": "search"},
        {"final_url": official, "authority": "OFFICIAL_AND_REACHABLE", "kind": "search"},
    ]
    assert pick_tor_fetch_urls(sources)[0] == official
    candidates = [
        {
            "url": official + "about",
            "is_onion": True,
            "same_host": True,
            "depth": 2,
            "authority": "OFFICIAL_AND_REACHABLE",
        },
        {"url": official, "is_onion": True, "same_host": True, "depth": 1},
    ]
    followed = pick_follow_urls(candidates, {official}, max_depth=3, limit=1)
    assert followed == [official + "about"]
    assert pick_tor_fetch_urls(sources, visited={official}) == [other]
    assert pick_follow_urls(candidates, {official, official + "about"}, max_depth=3, limit=1) == []


def test_empty_preferences_default_to_tor_auto():
    assert WebSettings.model_validate({}).tor_mode == "auto"
    assert WebSettings.model_validate({"tor_enabled": False}).tor_mode == "off"
    assert WebSettings.model_validate({"tor_enabled": True}).tor_mode == "auto"
