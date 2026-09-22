"""One normalized, honest snapshot of the five user-facing subsystems.

Every value is read from the subsystem that already owns it: compute readiness from
the RunPod controller's compact mapping, the local device from the pairing table, web
from the TinyFish configuration, Tor from the SOCKS5 endpoint the executor itself
uses, memory from the user's own preference. Nothing here starts compute, spends
money, downloads a model or probes a paid provider.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..compute.runpod_api import ERROR_MESSAGES
from ..config import Settings, get_settings
from ..models import Memory, User, now
from ..tools.local.devices import active_device, last_seen_seconds_ago, newest_device
from ..tools.models import PairedDevice
from ..tools.policy import WebSettings
from ..tools.policy import preferences as tool_preferences
from ..tools.tor.browser import socks_listening
from ..tools.tor.service import CIRCUIT_INVALID, DISABLED, NO_ENDPOINT, NOT_INSTALLED

READY = "ready"
STARTING = "starting"
CONFIGURED = "configured"
OFF = "off"
NOT_CONFIGURED = "not_configured"
UNAVAILABLE = "unavailable"
ERROR = "error"
DEGRADED = "degraded"

STATES = (READY, STARTING, CONFIGURED, OFF, NOT_CONFIGURED, UNAVAILABLE, ERROR, DEGRADED)

# Recommended recovery action per AI error code: (state, action, recoverable).
# `configure` needs a decision in settings; `retry` re-reads the authoritative state
# and lets the existing controller reconciliation continue. Nothing here starts compute.
AI_ERRORS = {
    "not_configured": (NOT_CONFIGURED, "configure", True),
    # Canalla Cloud (shared mode). An unreachable Gateway is recoverable; a rejected or
    # revoked installation needs the user to reconnect, not a retry.
    "gateway_not_connected": (NOT_CONFIGURED, "configure", True),
    "gateway_unavailable": (UNAVAILABLE, "retry", True),
    "gateway_busy": (UNAVAILABLE, "retry", True),
    "gateway_queue_full": (UNAVAILABLE, "retry", True),
    "gateway_request_in_flight": (UNAVAILABLE, "retry", True),
    "gateway_request_completed": (UNAVAILABLE, "retry", True),
    "gateway_protocol_mismatch": (ERROR, "configure", False),
    "gateway_auth_failed": (ERROR, "configure", False),
    "installation_revoked": (ERROR, "configure", False),
    "gateway_budget_denied": (ERROR, "configure", False),
    "gateway_managed_compute": (ERROR, "configure", False),
    "create_unknown": (DEGRADED, "retry", True),
    "external_compute": (DEGRADED, "retry", True),
    "multiple_compute": (ERROR, None, False),
    "COMPUTE_BUDGET_REACHED": (ERROR, "configure", False),
    "session_budget": (ERROR, "configure", False),
    "price_violation": (ERROR, "retry", True),
    "price_changed": (ERROR, "retry", True),
    "startup_failed": (ERROR, "retry", True),
    "startup_timeout": (ERROR, "retry", True),
    "malformed_response": (ERROR, "retry", True),
    "model_mismatch": (ERROR, "retry", True),
    "volume_mismatch": (ERROR, "configure", False),
    "llm_key_missing": (ERROR, "configure", False),
    "runpod_auth": (ERROR, "configure", False),
    "runpod_balance": (ERROR, "configure", False),
    "runpod_invalid_request": (ERROR, "configure", False),
    "no_compatible_gpu": (UNAVAILABLE, "retry", True),
    "price_limit": (UNAVAILABLE, "configure", True),
    "runpod_unavailable": (UNAVAILABLE, "retry", True),
    "runpod_timeout": (UNAVAILABLE, "retry", True),
    "runpod_rate_limit": (UNAVAILABLE, "retry", True),
    "connection_auth_failed": (UNAVAILABLE, "retry", True),
    "connection_failed": (UNAVAILABLE, "retry", True),
    "not_found": (UNAVAILABLE, "retry", True),
}

AI_DEFAULTS = {
    OFF: ("AI не запущен. GPU запускается только по вашему запросу.", None, False),
    STARTING: ("Запускаю AI…", None, True),
    READY: ("AI готов.", None, False),
    UNAVAILABLE: ("AI сейчас недоступен.", "retry", True),
    ERROR: ("Ошибка AI.", "retry", True),
    DEGRADED: ("AI ожидает решения.", "retry", True),
    NOT_CONFIGURED: ("RunPod не настроен. Добавьте API key в настройках Canalla LLM.", "configure", True),
}

# Shared mode has no local RunPod credential to configure: the same states are explained
# in terms of Canalla Cloud instead of a provider key.
CLOUD_DEFAULTS = {
    **AI_DEFAULTS,
    NOT_CONFIGURED: (
        "Canalla Cloud не подключён. Подключите его в настройках Canalla LLM.",
        "configure",
        True,
    ),
    UNAVAILABLE: ("Canalla Cloud сейчас недоступен.", "retry", True),
}

ACTION_KEYS = (None, "retry", "configure", "reconnect", "stop", "cancel_search")


def entry(state: str, message: str, *, detail_code=None, recoverable=False, action=None, details=None):
    assert state in STATES
    assert action in ACTION_KEYS
    return {
        "state": state,
        "message": message,
        "detail_code": detail_code,
        "recoverable": bool(recoverable),
        "action": action,
        "details": details or {},
    }


def preferences(db: Session, user_id: str) -> WebSettings:
    return tool_preferences(db, user_id)


def ai_status(controller, user: User):
    """AI readiness. `compact_ai` stays the single owner of the raw state machine.

    In shared mode ``controller`` is the Canalla Cloud adapter, which answers with the
    Gateway's own compact state, so both modes render through this one mapping.
    """
    defaults = CLOUD_DEFAULTS if getattr(controller, "shared", False) else AI_DEFAULTS
    payload = controller.llm_public_status(user)
    diagnostic = payload.get("diagnostic") or {}
    compute_state = diagnostic.get("compute_state")
    error_code = diagnostic.get("last_error")
    compact = payload.get("ai") or ERROR
    if compact in {READY, STARTING}:
        state, action, recoverable = compact, None, compact == STARTING
    elif compact == "off" and compute_state != "stopping":
        state, action, recoverable = OFF, None, False
    elif isinstance(error_code, str) and error_code in AI_ERRORS:
        state, action, recoverable = AI_ERRORS[error_code]
    elif compact == "waiting" and compute_state == "stopping":
        state, action, recoverable = OFF, None, False
    elif compact == "waiting":
        state, action, recoverable = DEGRADED, "retry", True
    elif compact == "unavailable":
        state, action, recoverable = NOT_CONFIGURED, "configure", True
        if bool(getattr(controller.api, "configured", False)):
            state, action, recoverable = UNAVAILABLE, "retry", True
    else:
        state, action, recoverable = ERROR, "retry", True
    message = (ERROR_MESSAGES.get(error_code) if error_code else None) or defaults[state][0]
    details = {
        "provider": payload.get("provider"),
        "model": payload.get("model"),
        "configured": bool(getattr(controller.api, "configured", False)),
        "compute_state": compute_state,
        "compact_ai": compact,
        "managed": diagnostic.get("managed"),
        "datacenter": diagnostic.get("datacenter"),
        "idle_deadline": diagnostic.get("idle_deadline"),
    }
    return entry(
        state,
        message,
        detail_code=error_code if isinstance(error_code, str) else None,
        recoverable=recoverable,
        action=action,
        details=details,
    )


def computer_status(db: Session, user: User, prefs: WebSettings, boot_time=None):
    """Local computer-use readiness: the device heartbeat decides health, and nothing else.

    ``ready`` needs a live heartbeat (the host loop's own signal, 45 s window). A host that was
    alive moments ago - or an app that just started and is still waiting for its first heartbeat -
    is ``starting`` ("подключается" / "восстанавливает соединение"), never a red failure: the
    reconnect is already in flight and the chip must not demand a button for it. Only after those
    windows does the honest ``host_offline`` with its manual recovery action appear. The computer
    *mode* is policy and never changes the health answer.
    """
    device = active_device(db, user.id)
    paired = bool(db.scalar(select(PairedDevice.id).where(PairedDevice.user_id == user.id).limit(1)))
    latest = device if device is not None else newest_device(db, user.id)
    age = last_seen_seconds_ago(latest) if latest is not None else None
    details = {
        "computer_mode": prefs.computer_mode,
        "paired": paired,
        "heartbeat_age_seconds": None if age is None else round(age, 1),
        "host_running": device is not None,
        "device": None,
    }
    if latest is not None:
        details["device"] = {
            "display_name": latest.display_name,
            "platform": latest.platform,
            "last_seen": latest.last_seen.isoformat() if latest.last_seen else None,
            "capabilities": latest.capabilities,
        }
    if device is not None:
        return entry(READY, "Компьютер готов.", details=details)
    if paired:
        settings = get_settings()
        gap = age is not None and age <= settings.host_reconnect_grace_seconds
        booting = (
            boot_time is not None
            and (now() - boot_time).total_seconds() <= settings.host_connect_grace_seconds
        )
        if gap or booting:
            return entry(
                STARTING,
                "Компьютер восстанавливает соединение…" if gap else "Компьютер подключается…",
                action="retry",
                recoverable=True,
                details=details,
            )
        return entry(
            UNAVAILABLE,
            "Подключённое устройство не отвечает.",
            detail_code="host_offline",
            action="reconnect",
            recoverable=True,
            details=details,
        )
    if prefs.computer_mode == "off":
        return entry(
            OFF,
            "Работа с компьютером выключена в настройках.",
            action="configure",
            recoverable=True,
            details=details,
        )
    return entry(
        NOT_CONFIGURED,
        "Компьютер не подключён.",
        detail_code="host_offline",
        action="reconnect",
        recoverable=True,
        details=details,
    )


def web_status(settings: Settings, prefs: WebSettings):
    """Web readiness. A configured provider is `configured`, never `ready`.

    Nothing here proves the provider answers: no TinyFish request is made for a chip.
    Real health is confirmed per request and surfaces as a tool error code.
    """
    configured = bool(settings.tinyfish_api_key.get_secret_value())
    enabled = bool(
        prefs.search_enabled
        or prefs.fetch_enabled
        or prefs.agent_mode != "off"
        or prefs.browser_mode != "off"
    )
    details = {
        "provider": "TinyFish",
        "configured": configured,
        "probe": "configuration",
        "search_enabled": prefs.search_enabled,
        "fetch_enabled": prefs.fetch_enabled,
        "agent_mode": prefs.agent_mode,
        "browser_mode": prefs.browser_mode,
        "search_fetch_free": settings.tinyfish_search_fetch_free,
        "tor_search_configured": bool(settings.tor_search_providers),
    }
    if not configured:
        return entry(
            NOT_CONFIGURED,
            "Веб не настроен. Добавьте TinyFish API key в настройках Canalla LLM.",
            detail_code="web_not_configured",
            action="configure",
            recoverable=True,
            details=details,
        )
    if not enabled:
        return entry(
            OFF,
            "Веб выключен в настройках.",
            action="configure",
            recoverable=True,
            details=details,
        )
    return entry(
        CONFIGURED,
        "Web настроен. Доступность провайдера проверяется при использовании.",
        details=details,
    )


def tor_status(settings: Settings, prefs: WebSettings, service=None):
    """Tor routing state: the service's real health, never the usage policy.

    ``ready`` means the route was proven: a SOCKS5h round trip through the endpoint in use came
    back as Tor (the proof is persisted, so this path stays read-only and never calls the network).
    A listening port without a proof is ``configured``, a service that is coming up is
    ``starting``/ ``reconnecting``, and everything else is ``unavailable`` with a typed reason.
    ``tor_mode`` is policy, not health: it is reported in ``details.mode`` and never turns a
    healthy service into an offline one. No clearnet fallback exists at any point.
    """
    snapshot = service.snapshot() if service is not None else None
    if snapshot is None:
        # No service registered (tests, or a very old deployment): keep the historical answer.
        listening = socks_listening(settings.tor_socks_host, settings.tor_socks_port)
        snapshot = {
            "state": "configured" if listening else "unavailable",
            "reason": None if listening else "tor_no_endpoint",
            "listening": listening,
            "verified_chain": False,
            "verified_at": None,
            "endpoint": None,
            "method": "socks5h",
            "binary": None,
            "proof_ttl_seconds": None,
        }
    endpoint = snapshot.get("endpoint") or {
        "host": settings.tor_socks_host,
        "port": settings.tor_socks_port,
    }
    details = {
        "mode": prefs.tor_mode,
        "proxy_host": endpoint["host"],
        "proxy_port": endpoint["port"],
        "configured_port": settings.tor_socks_port,
        "socks_listening": bool(snapshot.get("listening")),
        "verified_chain": bool(snapshot.get("verified_chain")),
        "verified_at": snapshot.get("verified_at"),
        "proof_ttl_seconds": snapshot.get("proof_ttl_seconds"),
        "method": snapshot.get("method"),
        "managed": bool(snapshot.get("managed")),
        "binary": snapshot.get("binary"),
        "candidates": snapshot.get("candidates") or [],
        "required": prefs.tor_mode == "on",
        "fallback": "none",
    }
    state = str(snapshot.get("state") or UNAVAILABLE)
    reason = snapshot.get("reason")
    if state == READY:
        return entry(
            READY,
            "Tor готов: цепь проверена."
            + (" Использование Tor выключено в настройках." if prefs.tor_mode == "off" else ""),
            details=details,
        )
    if state in {"starting", "reconnecting"}:
        return entry(
            STARTING,
            "Tor подключается…" if state == "starting" else "Tor восстанавливается…",
            action="retry",
            recoverable=True,
            details=details,
        )
    if state == "configured":
        return entry(
            CONFIGURED,
            "Tor отвечает, цепь ещё не проверена. Откат в clearnet не выполняется.",
            details=details,
        )
    messages = {
        NOT_INSTALLED: "Tor не найден на этом компьютере: установите Tor Browser или укажите tor.exe в настройках.",
        CIRCUIT_INVALID: "SOCKS отвечает, но цепь Tor не подтверждена. Запросы через Tor выполняться не будут.",
        DISABLED: "Управление Tor выключено в настройках сервера.",
        "tor_managed_disabled": "Управление Tor выключено в настройках сервера.",
        "tor_start_failed": "Не удалось запустить Tor.",
        NO_ENDPOINT: "Tor запущен, но SOCKS не отвечает. Запросы через Tor выполняться не будут.",
    }
    detail_code = str(reason or NO_ENDPOINT)
    return entry(
        UNAVAILABLE,
        messages.get(detail_code, "Tor не подтверждён. Запросы через Tor выполняться не будут"),
        detail_code=detail_code,
        action="retry",
        recoverable=True,
        details=details,
    )


def memory_status(db: Session, user: User, settings: Settings):
    """Memory readiness. Retrieval is lexical, so no model download is involved."""
    details = {
        "enabled": bool(user.use_memory),
        "relevant_memory": bool(user.relevant_memory),
        "max_items": min(int(user.max_memories), settings.memory_max_items),
        "retrieval": "lexical",
        "items": None,
    }
    if not user.use_memory:
        return entry(
            OFF,
            "Память выключена в профиле.",
            action="configure",
            recoverable=True,
            details=details,
        )
    try:
        items = db.scalar(
            select(func.count())
            .select_from(Memory)
            .where(Memory.user_id == user.id, Memory.is_active.is_(True))
        )
    except Exception:
        return entry(
            UNAVAILABLE,
            "Память включена, но подсистема не ответила.",
            detail_code="memory_unavailable",
            action="retry",
            recoverable=True,
            details=details,
        )
    details["items"] = int(items or 0)
    return entry(READY, "Память включена.", details=details)


def subsystem_status(
    db: Session, user: User, controller, settings: Settings, tor_service=None, boot_time=None
):
    prefs = preferences(db, user.id)
    return {
        "ai": ai_status(controller, user),
        "computer": computer_status(db, user, prefs, boot_time),
        "web": web_status(settings, prefs),
        "tor": tor_status(settings, prefs, tor_service),
        "memory": memory_status(db, user, settings),
    }
