"""Compact AI runtime mapping. Compute internals stay on the controller."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

LABELS = {
    "off": "AI Off",
    "starting": "AI Starting",
    "ready": "AI Ready",
    "waiting": "AI Waiting",
    "unavailable": "AI Unavailable",
    "error": "AI Error",
}

COMPUTE_TO_AI = {
    "offline": "off",
    "stopped": "off",
    "not_configured": "unavailable",
    "searching": "starting",
    "gpu_found": "starting",
    "creating": "starting",
    "starting_pod": "starting",
    "starting_environment": "starting",
    "mounting_storage": "starting",
    "starting_llm": "starting",
    "connecting": "starting",
    "loading_model": "starting",
    "generating": "ready",
    "ready": "ready",
    "stopping": "waiting",
    "create_unknown": "waiting",
    "external_compute": "waiting",
    "multiple_compute": "error",
    "error": "error",
}

STARTING = {
    "searching",
    "gpu_found",
    "creating",
    "starting_pod",
    "starting_environment",
    "mounting_storage",
    "starting_llm",
    "connecting",
    "loading_model",
}

# What a search that found nothing means. Two of these are conclusions (the catalogue has no
# compatible card, or the only cards are above the user's own maximum) and can never become a
# transition by waiting. `gpu_unavailable` is the one transient case: capacity comes back, so a
# *live, bounded* search operation may honestly be reported as a transition — and without one it
# is the same failure as the others.
SEARCH_CONCLUSIONS = {"no_compatible_gpu", "price_limit"}
TRANSIENT_SEARCH = "gpu_unavailable"
SEARCH_FAILURES = SEARCH_CONCLUSIONS | {TRANSIENT_SEARCH}


def compact_ai(
    *,
    provider: str,
    app_env: str,
    configured: bool,
    compute_state: str,
    error_code: str | None,
    search_active: bool = False,
):
    """One compute state -> AI state mapping, shared by direct mode and the Gateway.

    ``search_active`` says whether a *real* search operation is in flight right now, with an
    identity and a deadline (the Gateway persists both; direct mode schedules its retry). A
    `searching` state without one is a leftover, not a promise of green, and must never be
    rendered as «Connecting» for an unbounded time: it collapses to the honest failure code.
    """
    if provider == "mock":
        if app_env == "production":
            return "unavailable", LABELS["unavailable"]
        return "ready", LABELS["ready"]
    if not configured or compute_state == "not_configured":
        return "unavailable", LABELS["unavailable"]
    if error_code in SEARCH_FAILURES and compute_state in {"searching", "offline", "error"}:
        live = compute_state == "searching" and search_active and error_code == TRANSIENT_SEARCH
        if not live:
            return "unavailable", LABELS["unavailable"]
    if error_code == "COMPUTE_BUDGET_REACHED":
        return "error", LABELS["error"]
    ai = COMPUTE_TO_AI.get(compute_state, "error" if error_code else "off")
    return ai, LABELS[ai]


def idle_deadline(session, at):
    if session is None or not session.auto_stop_minutes or session.status not in {"ready", "generating"}:
        return None
    from .controller import utc

    return (utc(session.last_activity_at) + timedelta(minutes=session.auto_stop_minutes)).isoformat()


def money_prompt(gpu_name: str, hourly: Decimal) -> str:
    name = gpu_name.replace("NVIDIA ", "") if gpu_name else "GPU"
    return f"Запустить AI на {name} примерно за ${hourly:.2f}/ч?"
