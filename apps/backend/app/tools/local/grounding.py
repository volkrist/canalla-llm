"""Deterministic final-answer grounding. Prompt is not the only control."""

import re

from .facts import latest, public_block, store

DENIAL = re.compile(
    r"(?i)("
    r"no (filesystem|file|disk|local) access|don't have (filesystem|file) access|"
    r"cannot access (the )?(file|filesystem|disk|computer)|"
    r"не (могу|имею) .{0,24}(доступ|файл|файлов(ой|ую)|диск)|"
    r"нет доступа к (файл|диск|компьютер)|"
    r"i (can't|cannot) (read|access|open) (the )?file|"
    r"run systeminfo yourself|запустите systeminfo|"
    r"you need to install jq yourself"
    r")"
)
BROWSER_DENIAL = re.compile(
    r"(?i)("
    r"don't have a browser|do not have a browser|no browser tool|"
    r"нет браузер|браузер недоступ|i (can't|cannot|don't) .{0,24}browser"
    r")"
)
HEX64 = re.compile(r"\b[a-fA-F0-9]{64}\b")


def contradiction(text: str, facts: dict) -> str | None:
    value = text or ""
    items = store(facts)
    if not items:
        return None
    if DENIAL.search(value):
        if any(
            item.get("kind")
            in {
                "FILE_READ",
                "FILE_CREATED",
                "FILE_WRITTEN",
                "HASH_RESULT",
                "SYSTEM_INFO",
                "DIRECTORY_CREATED",
                "SOFTWARE_INSTALLED",
            }
            for item in items
        ):
            return "denies_verified_local_access"
    if latest(facts, "SOFTWARE_INSTALLED") and re.search(
        r"(?i)install jq yourself|установите jq сами|you need to install", value
    ):
        return "ignores_verified_install"
    if BROWSER_DENIAL.search(value) and any(
        item.get("kind") in {"BROWSER_PAGE", "BROWSER_ERROR"} for item in items
    ):
        return "denies_verified_browser"
    return None


def incomplete(text: str, facts: dict, prompt: str) -> str | None:
    value = text or ""
    asked = prompt or ""
    digest = (latest(facts, "HASH_RESULT") or {}).get("digest") or ""
    if (
        HEX64.fullmatch(str(digest))
        and re.search(r"(?i)sha256|хеш|hash", asked)
        and digest.casefold() not in value.casefold()
    ):
        return "missing_hash"
    read = latest(facts, "FILE_READ")
    excerpt = (read or {}).get("content_excerpt") or ""
    written = latest(facts, "FILE_WRITTEN") or latest(facts, "FILE_CREATED")
    written_excerpt = (written or {}).get("content_excerpt") or ""
    if (
        excerpt
        and re.search(r"(?i)прочитай|перечитай|что внутри|что записано|read (the )?file|содержим", asked)
        and excerpt[:24].strip()
        and excerpt[:24].strip() not in value
    ):
        return "missing_file_content"
    if (
        written_excerpt
        and re.search(r"(?i)создай|запис|create|write|с текстом|with(?: the)? text", asked)
        and written_excerpt[:24].strip()
        and written_excerpt[:24].strip() not in value
    ):
        return "missing_file_content"
    info = latest(facts, "SYSTEM_INFO")
    if info and re.search(r"(?i)windows|cpu|ram|диск", asked):
        token = str(info.get("windows_version") or info.get("cpu") or "")
        if token and token not in value:
            return "missing_system_info"
    found = latest(facts, "FILE_FOUND")
    if found and re.search(r"(?i)найди|имя файла|which file", asked):
        name = str(found.get("path") or "").replace("\\", "/").rsplit("/", 1)[-1]
        if name and name.casefold() not in value.casefold():
            return "missing_search_filename"
    pages = [item for item in store(facts) if item.get("kind") == "BROWSER_PAGE"]
    if pages and re.search(r"(?i)браузер|browser|заголовок|title|документ|documentation", asked):
        needed = pages
        if re.search(r"(?i)документ|documentation|docs", asked):
            docs = [
                item
                for item in pages
                if re.search(r"(?i)/doc|document", f"{item.get('url') or ''} {item.get('title') or ''}")
            ]
            if docs:
                needed = docs
        if any(
            str(item.get("title") or "").strip() and str(item.get("title") or "").strip()[:16] not in value
            for item in needed
        ):
            return "missing_browser_title"
    error = latest(facts, "BROWSER_ERROR")
    if (
        error
        and re.search(r"(?i)браузер|browser", asked)
        and "browser navigation failed" not in value.casefold()
    ):
        return "missing_browser_error"
    return None


def issue_for(text: str, facts: dict, prompt: str) -> str | None:
    return contradiction(text, facts) or incomplete(text, facts, prompt)


def repair_prompt(facts: dict, issue: str) -> str:
    return (
        "Your response conflicts with verified tool results or omits a requested verified value "
        f"({issue}). Rewrite using VERIFIED_RESULTS only. Do not rerun actions. "
        "Do not claim lack of access.\n\n" + public_block(facts)
    )


def fallback_answer(facts: dict, prompt: str) -> str:
    asked = prompt or ""
    lines = []
    found = latest(facts, "FILE_FOUND")
    if found and re.search(r"(?i)найди|имя файла|which file", asked):
        path = found.get("path") or ""
        name = str(path).replace("\\", "/").rsplit("/", 1)[-1]
        lines.append(f"Файл: {name}" + (f" ({path})" if path else ""))
    digest_item = latest(facts, "HASH_RESULT")
    digest = str((digest_item or {}).get("digest") or "")
    if digest_item and re.search(r"(?i)sha256|хеш|hash", asked) and HEX64.fullmatch(digest):
        lines.append(digest)
    read = latest(facts, "FILE_READ")
    if read and re.search(r"(?i)прочитай|перечитай|что внутри|что записано|read (the )?file|содержим", asked):
        content = (read.get("content_excerpt") or "").strip()
        path = read.get("path") or ""
        if path:
            lines.append(f"Прочитан файл: {path}")
        if content:
            lines.append(content)
    created = latest(facts, "FILE_CREATED") or latest(facts, "FILE_WRITTEN")
    if created and re.search(r"(?i)создай|запис|create|write|с текстом|with(?: the)? text", asked):
        lines.append(f"Создан файл: {created.get('path')}")
        excerpt = (created.get("content_excerpt") or "").strip() or (
            (read or {}).get("content_excerpt") or ""
        ).strip()
        if excerpt:
            lines.append(excerpt)
        if created.get("expected_sha256"):
            lines.append(f"sha256={created.get('expected_sha256')} verified={created.get('verified')}")
    info = latest(facts, "SYSTEM_INFO")
    if info and re.search(r"(?i)windows|cpu|ram|диск|system", asked):
        lines.append(
            f"Windows: {info.get('windows_version')}; CPU: {info.get('cpu')}; "
            f"RAM: {info.get('ram')}; disk: {info.get('disk')}"
        )
    installed = latest(facts, "SOFTWARE_INSTALLED")
    if installed:
        lines.append(f"{installed.get('package_id')} {installed.get('version')}".strip())
    stopped = latest(facts, "PROCESS_STOPPED")
    started = latest(facts, "PROCESS_STARTED")
    if stopped:
        lines.append(f"Процесс остановлен, pid={stopped.get('pid')}")
    elif started and re.search(r"(?i)запусти|процесс", asked):
        lines.append(f"Процесс запущен, pid={started.get('pid')}")
    page = latest(facts, "BROWSER_PAGE")
    pages = [item for item in store(facts) if item.get("kind") == "BROWSER_PAGE"]
    if pages:
        for item in pages:
            title = item.get("title") or ""
            url = item.get("url") or ""
            if title or url:
                lines.append(f"{title} {url}".strip())
    elif page:
        title = page.get("title") or ""
        url = page.get("url") or ""
        if title or url:
            lines.append(f"{title} {url}".strip())
    error = latest(facts, "BROWSER_ERROR")
    if error:
        lines.append(
            f"browser navigation failed: {error.get('error_code') or 'browser_error'} "
            f"status={error.get('session_status') or 'FAILED'}"
        )
    if not lines:
        block = public_block(facts)
        return block or "Инструменты выполнены, но проверяемых фактов нет."
    return "\n".join(line for line in lines if line).strip()


def goal_met(facts: dict, prompt: str) -> bool:
    asked = prompt or ""
    digest = str((latest(facts, "HASH_RESULT") or {}).get("digest") or "")
    if HEX64.fullmatch(digest) and re.search(r"(?i)sha256|хеш|hash", asked):
        return True
    if latest(facts, "FILE_FOUND") and re.search(r"(?i)найди|имя файла|which file", asked):
        return True
    if latest(facts, "SYSTEM_INFO") and re.search(r"(?i)windows|cpu|ram|диск|system info", asked):
        return True
    if latest(facts, "FILE_READ") and re.search(
        r"(?i)прочитай|перечитай|что внутри|что записано|read (the )?file|содержим", asked
    ):
        return True
    written = latest(facts, "FILE_CREATED") or latest(facts, "FILE_WRITTEN")
    if (
        written
        and written.get("verified") is not False
        and re.search(r"(?i)создай|запис|create|write|с текстом|with(?: the)? text", asked)
    ):
        return True
    if latest(facts, "PROCESS_STOPPED") and re.search(r"(?i)останови процесс|stop (the )?process", asked):
        return True
    pages = [item for item in store(facts) if item.get("kind") == "BROWSER_PAGE"]
    if pages and re.search(r"(?i)браузер|browser|python\.org", asked):
        if re.search(r"(?i)документ|documentation|docs", asked):
            return (
                any(
                    re.search(r"(?i)/doc|document", f"{item.get('url') or ''} {item.get('title') or ''}")
                    for item in pages
                )
                and len({str(item.get("url") or "") for item in pages}) >= 2
            )
        return True
    return False


def browser_goal_remaining(facts: dict, prompt: str) -> list[str]:
    asked = prompt or ""
    if not re.search(r"(?i)браузер|browser|открой.{0,40}python|python\.org", asked):
        return []
    pages = [item for item in store(facts) if item.get("kind") == "BROWSER_PAGE"]
    remaining = []
    if not pages:
        remaining.append("open_start")
    if re.search(r"(?i)документ|documentation|docs", asked):
        if not any(
            re.search(r"(?i)/doc|document", f"{item.get('url') or ''} {item.get('title') or ''}")
            for item in pages
        ):
            remaining.append("navigate_documentation")
        elif re.search(r"(?i)заголовок|title", asked) and not any(item.get("title") for item in pages[1:]):
            remaining.append("read_docs_title")
    return remaining


def simple_factual(prompt: str) -> bool:
    return bool(
        re.search(
            r"(?i)("
            r"прочитай|перечитай|что внутри|что записано|sha256|хеш|hash|"
            r"версию windows|cpu|ram|свободное место|"
            r"найди в этой папке|имя файла|"
            r"создай файл|create .{0,40}file|с текстом|with(?: the)? text|"
            r"создай файл.{0,80}прочитай|"
            r"останови процесс|запусти.{0,40}(процесс|python|sleep)|"
            r"открой в браузере|посмотри страницу в браузере|перейди по ссылке"
            r")",
            prompt or "",
        )
    )
