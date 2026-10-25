"""User-visible assistant text. Never surface raw tool protocol."""

import re

TOOL_PROTOCOL = re.compile(
    r"(?is)("
    r"<tool_call\b.*?(?:</tool_call>|$)|"
    r"</?function(?:_call)?\b.*|"
    r"```(?:json|xml|tool)?\s*\{\s*\"tool_calls\".*"
    r")"
)
TOOL_START = re.compile(
    r"(?is)(<tool_call\b|</?function(?:_call)?\b|```(?:json|xml|tool)?\s*\{\s*\"tool_calls\")"
)

HALT_MESSAGES = {
    "task_paused": "Paused while preparing next action.",
    "waiting_workspace": "Workspace занят другой задачей. Эта задача в очереди.",
    "waiting_confirmation": "Waiting for confirmation.",
    "host_offline": "Device offline. Task will continue after the computer reconnects.",
    "llm_unavailable": "LLM unavailable. Task will continue after recovery.",
    "tools_unsupported": (
        "Модель отклонила вызовы инструментов (tools_unsupported). Ни один инструмент не выполнялся."
    ),
    "browser_no_page_result": (
        "Страница не получена: браузерный инструмент не вернул результат (browser_no_page_result)."
    ),
    "task_stopped": "Task stopped.",
}

# The technical cause, named honestly, without pretending the work did not happen.
HALT_REASONS = {
    "llm_unavailable": "модель не ответила вовремя (llm_stalled)",
    "host_offline": "потеряна связь с Computer host (host_offline)",
}


def strip_tool_protocol(text: str) -> str:
    value = str(text or "")
    value = TOOL_PROTOCOL.sub("", value)
    if TOOL_START.search(value):
        value = TOOL_START.split(value, maxsplit=1)[0]
    return value.strip()


def _verified_lines(facts: dict, limit: int = 6) -> list[str]:
    """A short, user-facing list of what real tools already did.

    Only verified facts are used. The line never repeats the same path for the same kind, and no
    file content is quoted: this text appears in the chat next to a recoverable task.
    """
    from .facts import store

    labels = {
        "FILE_CREATED": "создан файл",
        "FILE_WRITTEN": "изменён файл",
        "FILE_READ": "прочитан файл",
        "DIRECTORY_CREATED": "создана папка",
        "SOFTWARE_INSTALLED": "установлено ПО",
    }
    seen = set()
    out: list[str] = []
    for item in reversed(store(facts or {})):
        kind = str(item.get("kind") or "")
        label = labels.get(kind)
        if label and item.get("path"):
            target = str(item.get("path"))
        elif kind == "PROCESS_STARTED" and item.get("pid"):
            label, target = "запущен процесс", f"pid={item.get('pid')}"
        elif kind == "BROWSER_PAGE" and item.get("url"):
            label, target = "открыта страница", str(item.get("url"))
        elif kind == "RESEARCH_SOURCE" and item.get("url"):
            label, target = "прочитан источник", str(item.get("url"))
        else:
            continue
        key = (label, target.casefold())
        if key in seen:
            continue
        seen.add(key)
        out.append(f"{label}: {target}")
        if len(out) >= limit:
            break
    return list(reversed(out))


def progress_summary(facts: dict, halt: str | None) -> str:
    lines = _verified_lines(facts)
    if not lines:
        return ""
    reason = HALT_REASONS.get(halt or "")
    head = "Задача сохранена и продолжится автоматически."
    if reason:
        head = f"Итоговый текст не сформирован: {reason}. {head}"
    body = "\n".join(f"• {line}" for line in lines)
    return (
        f"{head}\n"
        "Уже выполнено настоящими инструментами, результаты сохранены:\n"
        f"{body}\n"
        "Итоговый ответ появится в этом чате после восстановления — повторять действия не нужно."
    )


def public_assistant_text(text: str, halt: str | None = None, facts: dict | None = None) -> str:
    if halt in {"llm_unavailable", "host_offline"}:
        summary = progress_summary(facts, halt)
        if summary:
            return summary
    if halt in HALT_MESSAGES:
        return HALT_MESSAGES[halt]
    cleaned = strip_tool_protocol(text)
    return cleaned
