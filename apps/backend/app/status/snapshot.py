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
from ..config import Settings
from ..models import Memory, User
from ..tools.local.devices import active_device
from ..tools.models import PairedDevice
from ..tools.policy import WebSettings
from ..tools.policy import preferences as tool_preferences
from ..tools.tor.browser import socks_listening

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


def computer_status(db: Session, user: User, prefs: WebSettings):
    """Local computer-use readiness. Liveness is the device heartbeat window, not the Desktop."""
    device = active_device(db, user.id)
    paired = bool(db.scalar(select(PairedDevice.id).where(PairedDevice.user_id == user.id).limit(1)))
    details = {
        "computer_mode": prefs.computer_mode,
        "paired": paired,
        "device": None,
    }
    if device is not None:
        details["device"] = {
            "display_name": device.display_name,
            "platform": device.platform,
            "last_seen": device.last_seen.isoformat() if device.last_seen else None,
            "capabilities": device.capabilities,
        }
    if prefs.computer_mode == "off":
        return entry(
            OFF,
            "Работа с компьютером выключена в настройках.",
            action="configure",
            recoverable=True,
            details=details,
        )
    if device is not None:
        return entry(READY, "Компьютер готов.", details=details)
    if paired:
        return entry(
            UNAVAILABLE,
            "Подключённое устройство не отвечает.",
            detail_code="host_offline",
            action="reconnect",
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


def tor_status(settings: Settings, prefs: WebSettings):
    """Tor routing state. Fail-closed, and never `ready` without a verified chain.

    An open SOCKS5 port is not a verified Tor route, so a listening endpoint is
    reported as `configured` ("доступен, цепь ещё не проверена"). The architecture
    stores no authoritative last-verified proof: `prove_socks5()` runs inside
    TorBrowserController.start_session and its result lives only in that connection,
    and this status path must not poll it. Therefore `ready` is intentionally never
    produced for Tor, and no clearnet fallback exists at any point.
    """
    listening = socks_listening(settings.tor_socks_host, settings.tor_socks_port)
    details = {
        "mode": prefs.tor_mode,
        "proxy_host": settings.tor_socks_host,
        "proxy_port": settings.tor_socks_port,
        "socks_listening": listening,
        "verified_chain": False,
        "proof_store": "none",
        "required": prefs.tor_mode == "on",
        "fallback": "none",
    }
    if prefs.tor_mode == "off":
        return entry(
            OFF,
            "Tor выключен в настройках.",
            action="configure",
            recoverable=True,
            details=details,
        )
    if listening:
        return entry(
            CONFIGURED,
            "Tor доступен, цепь ещё не проверена. Откат в clearnet не выполняется.",
            details=details,
        )
    return entry(
        UNAVAILABLE,
        "Tor не подтверждён. Запросы через Tor выполняться не будут, откат в clearnet не выполняется.",
        detail_code="tor_unavailable",
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


def subsystem_status(db: Session, user: User, controller, settings: Settings):
    prefs = preferences(db, user.id)
    return {
        "ai": ai_status(controller, user),
        "computer": computer_status(db, user, prefs),
        "web": web_status(settings, prefs),
        "tor": tor_status(settings, prefs),
        "memory": memory_status(db, user, settings),
    }
