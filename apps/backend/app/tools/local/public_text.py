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
    "task_stopped": "Task stopped.",
}


def strip_tool_protocol(text: str) -> str:
    value = str(text or "")
    value = TOOL_PROTOCOL.sub("", value)
    if TOOL_START.search(value):
        value = TOOL_START.split(value, maxsplit=1)[0]
    return value.strip()


def public_assistant_text(text: str, halt: str | None = None) -> str:
    if halt in HALT_MESSAGES:
        return HALT_MESSAGES[halt]
    cleaned = strip_tool_protocol(text)
    return cleaned
