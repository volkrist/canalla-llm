"""Token accounting for the composer's context indicator.

Only the inference server knows the true token count of a prompt. Alex therefore
reports what it can prove without a GPU: a deterministic estimate over exactly the
pieces :meth:`app.context_builder.ContextBuilder.build` puts into the model context,
labelled per part, so the composer can show what consumes the window. When a
generation has already run, the measured prompt size reported by the provider is
returned next to the estimate so the two can be compared.

Calibration constants are the documented rule of thumb for a Qwen-class BPE on mixed
Russian/English text (four latin characters per token, two cyrillic ones) plus the
small chat-template overhead every message carries. They are estimates, not a
tokenizer, and the UI says so (docs/context-usage.md).
"""

ASCII_CHARS_PER_TOKEN = 4
WIDE_CHARS_PER_TOKEN = 2
MESSAGE_OVERHEAD_TOKENS = 4

# Keys are stable identifiers for tests and for the UI breakdown order.
PART_SYSTEM = "system"
PART_PROFILE = "profile"
PART_PINNED = "pinned"
PART_PROJECT = "project"
PART_PROJECT_MEMORY = "project_memory"
PART_MEMORY = "memory"
PART_DOCUMENTS = "documents"
PART_HISTORY = "history"
PART_DRAFT = "draft"

# Display labels for the composer breakdown; the order here is the order the parts are
# appended in, and the UI keeps only the parts that carry content.
LABELS = {
    PART_SYSTEM: "System",
    PART_PROFILE: "Profile & instructions",
    PART_PINNED: "Pinned memory",
    PART_PROJECT: "Project",
    PART_PROJECT_MEMORY: "Project memory",
    PART_MEMORY: "Memories",
    PART_DOCUMENTS: "Documents",
    PART_HISTORY: "History",
    PART_DRAFT: "Current draft",
}


def _ceil_div(value: int, divisor: int) -> int:
    return -(-value // divisor)


def estimate_tokens(text: str | None) -> int:
    """Deterministic character-based estimate of the tokens in one message body."""
    content = text or ""
    if not content:
        return 0
    wide = sum(1 for character in content if ord(character) > 127)
    narrow = len(content) - wide
    return _ceil_div(narrow, ASCII_CHARS_PER_TOKEN) + _ceil_div(wide, WIDE_CHARS_PER_TOKEN)


def part(key: str, label: str, text: str | None) -> dict:
    """One labelled block of the model context. Empty blocks are still returned."""
    content = text or ""
    tokens = estimate_tokens(content) + MESSAGE_OVERHEAD_TOKENS if content else 0
    return {"key": key, "label": label, "chars": len(content), "tokens": tokens}


def messages_part(key: str, label: str, contents: list[str]) -> dict:
    """One labelled block made of several chat messages (history), overhead included."""
    bodies = [content for content in contents if content]
    return {
        "key": key,
        "label": label,
        "chars": sum(len(body) for body in bodies),
        "tokens": sum(estimate_tokens(body) + MESSAGE_OVERHEAD_TOKENS for body in bodies),
    }


def summarize(
    parts: list[dict],
    *,
    limit_tokens: int,
    model: str,
    measured: dict | None = None,
) -> dict:
    """Fold the labelled parts into the payload the composer renders."""
    limit = max(1, int(limit_tokens or 0))
    used = sum(int(item.get("tokens") or 0) for item in parts)
    return {
        "model": model,
        "limit_tokens": limit,
        "used_tokens": used,
        "remaining_tokens": max(0, limit - used),
        "percent": round(used / limit * 100, 1),
        "estimated": True,
        "method": "chars_per_token",
        "parts": [item for item in parts if item["chars"]],
        "measured": measured,
    }
