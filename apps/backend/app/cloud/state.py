"""One cached, read-only view of Alex Cloud for the whole local backend.

The Gateway is the authority for shared compute state and for the shared RunPod
balance, so this module keeps the last answer and refreshes it on a TTL. Nothing here
starts, resumes or stops compute: the only operations that change anything are the
explicit ``ensure``/``stop`` calls, and they are typed Gateway operations.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from ..config import Settings
from .client import CloudError, GatewayClient, utc

logger = logging.getLogger(__name__)

ACTIVE_STATES = {"searching", "gpu_found", "creating", "starting_pod", "loading_model", "ready", "generating"}

CLOUD_STATES = ("connected", "connecting", "not_connected", "unavailable", "revoked", "protocol_mismatch")


def search_active(compute: dict | None, *, at: datetime) -> bool:
    """Does the Gateway report a *real* bounded search operation right now?

    A `searching` state is only a transition while the Gateway can name the operation and its
    deadline has not passed. Anything else — a state with no operation, an expired deadline, or
    a Gateway that predates the field — is not proof that something is happening, and the
    caller must treat it as the failure it is. Proof is never invented here.
    """
    search = (compute or {}).get("search")
    if not isinstance(search, dict) or search.get("active") is not True:
        return False
    operation = search.get("operation_id")
    if not isinstance(operation, str) or not operation:
        # No identity: nothing to point at, so nothing is happening.
        return False
    deadline = search.get("deadline")
    if not isinstance(deadline, str):
        return True
    try:
        parsed = datetime.fromisoformat(deadline)
    except ValueError:
        return False
    return utc(parsed) > utc(at)


class CloudState:
    def __init__(self, settings: Settings, *, client: GatewayClient | None = None, clock=None):
        self.settings = settings
        self.client = client or GatewayClient(settings)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = asyncio.Lock()
        self._task = None
        self.compute: dict | None = None
        self.balance: dict | None = None
        self.gateway_version: str | None = None
        self.protocol_ok = True
        self.error_code: str | None = None
        self.message: str | None = None
        self.fetched_at: datetime | None = None
        self.last_success_at: datetime | None = None
        self.fetches = 0  # observable for tests

    # ------------------------------------------------------------------ configuration

    @property
    def shared(self) -> bool:
        return self.settings.alex_ai_mode == "shared"

    @property
    def configured(self) -> bool:
        return self.client.configured

    def interval(self) -> float:
        state = (self.compute or {}).get("state")
        if state in ACTIVE_STATES:
            return float(self.settings.gateway_status_active_seconds)
        return float(self.settings.gateway_status_idle_seconds)

    def _age(self) -> float | None:
        if self.fetched_at is None:
            return None
        return (utc(self.clock()) - utc(self.fetched_at)).total_seconds()

    # ---------------------------------------------------------------------- refresh

    async def refresh(self, *, force: bool = False) -> None:
        if not self.shared:
            return
        if not self.configured:
            self.error_code, self.message = "gateway_not_connected", "Canalla Cloud не подключён."
            return
        age = self._age()
        if not force and age is not None and age < self.interval():
            return
        async with self._lock:
            age = self._age()
            if force or age is None or age >= self.interval():
                await self._read()

    async def _read(self) -> None:
        """One read-only Gateway round trip. It never starts compute."""
        self.fetches += 1
        try:
            await self.client.health()
            self.compute = await self.client.compute_status()
            self.balance = await self.client.balance()
        except CloudError as error:
            self.error_code, self.message = error.code, error.message
            self.protocol_ok = error.code != "gateway_protocol_mismatch"
        except Exception:
            self.error_code, self.message = "gateway_unavailable", None
        else:
            self.error_code = self.message = None
            self.protocol_ok = True
            self.fetched_at = self.last_success_at = self.clock()

    async def start(self) -> None:
        if self._task or not self.shared:
            return
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if not self._task:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.warning("cloud_state_loop_stop_failure")
        self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.refresh()
            except Exception:
                logger.warning("cloud_state_refresh_failure")
            await asyncio.sleep(self.interval())

    # ---------------------------------------------------------------------- readouts

    def state(self) -> str:
        if not self.shared:
            return "not_connected"
        if not self.configured:
            return "not_connected"
        if self.error_code == "installation_revoked":
            return "revoked"
        if self.error_code == "gateway_protocol_mismatch":
            return "protocol_mismatch"
        if self.error_code:
            return "unavailable"
        if self.compute is None:
            return "connecting"
        return "connected"

    def compute_state(self) -> str | None:
        return (self.compute or {}).get("state")

    def active(self) -> bool:
        """Is shared compute busy? Decides the balance refresh interval only."""
        return self.compute_state() in ACTIVE_STATES

    def available(self) -> bool:
        return bool(self.shared and self.configured and not self.error_code and self.compute is not None)

    def snapshot(self) -> dict:
        state = self.state()
        return {
            "mode": self.settings.alex_ai_mode,
            "state": state,
            "message": self.message or _MESSAGES.get(state),
            "detail_code": self.error_code or ("gateway_not_connected" if state == "not_connected" else None),
            "url": self.client.url or None,
            "default_url": None,
            "installation_id": self.client.installation_id or None,
            "enrolled": bool(self.client.configured),
            "reachable": state == "connected",
            "protocol_version": self.settings.alex_gateway_protocol_version,
            "balance_source": "gateway" if self.shared else "runpod_direct",
            "recoverable": state in {"unavailable", "connecting"},
            "action": "retry" if state == "unavailable" else None,
            "details": {
                "gateway_version": self.gateway_version,
                "protocol_ok": self.protocol_ok,
                "compute_state": self.compute_state(),
                "fetched_at": None if self.fetched_at is None else utc(self.fetched_at).isoformat(),
                "read_only": True,
            },
        }


_MESSAGES = {
    "connected": "Canalla Cloud подключён. AI работает через общий Gateway.",
    "connecting": "Подключаемся к Canalla Cloud…",
    "not_connected": "Canalla Cloud не подключён.",
    "unavailable": "Canalla Cloud сейчас недоступен.",
    "revoked": "Эта установка отключена от Canalla Cloud. Нужен новый код активации.",
    "protocol_mismatch": "Версия Canalla Cloud несовместима с этой установкой Canalla LLM.",
}


class CloudAi:
    """Adapter that lets the shared-mode AI chip reuse the existing status logic.

    ``subsystem_status``/``ai_status`` only need ``llm_public_status(user)`` and an
    ``api.configured`` flag; this provides exactly that, from the Gateway snapshot, so
    shared mode never invents a second status vocabulary.

    The Gateway owns the compact ``ai`` value, and it is trusted *except* where it would claim
    more than the raw state proves: a ``searching`` state with no live bounded operation is a
    leftover, not a transition, and is reported as the capacity failure it is. That is what
    kept an installed client amber for 23 minutes against a Gateway that only ever answered
    ``searching``.
    """

    def __init__(self, cloud: CloudState):
        self.cloud = cloud
        self.api = self

    @property
    def shared(self) -> bool:
        return True

    @property
    def configured(self) -> bool:
        return bool(self.cloud.configured and self.cloud.error_code not in {"installation_revoked"})

    def search_active(self) -> bool:
        return search_active(self.cloud.compute, at=self.cloud.clock())

    def llm_public_status(self, user) -> dict:
        compute = self.cloud.compute or {}
        last_error = self.cloud.error_code or compute.get("error_code")
        search = compute.get("search") if isinstance(compute.get("search"), dict) else {}
        state = compute.get("state")
        live_search = self.search_active()
        gateway_ai = compute.get("ai")
        ai = gateway_ai
        if ai is None:
            # No successful Gateway read yet: an unenrolled or unreachable installation is
            # `unavailable` (which the chip layer renders as "Alex Cloud не подключён"),
            # never a bare `off` that would hide the reason.
            ai = "off" if self.cloud.configured and not last_error else "unavailable"
        elif ai == "starting" and state == "searching" and not live_search:
            # The Gateway (or an older one, which never reported the search operation at all)
            # says `starting` for a search that is not running: never render that as amber.
            ai = "unavailable"
        return {
            "ai": ai,
            # Never relay a label that belongs to a state this adapter just corrected.
            "ai_label": compute.get("ai_label") if ai == gateway_ai else None,
            "provider": "alex-cloud",
            "model": self.cloud.settings.llm_model,
            "diagnostic": {
                "compute_state": state,
                "last_error": last_error,
                "managed": compute.get("managed"),
                "adopted": compute.get("adopted"),
                "idle_deadline": compute.get("idle_deadline"),
                "search_active": live_search,
                "search_deadline": search.get("deadline"),
                "datacenter": None,
                "queue": compute.get("queue"),
                "session": compute.get("session"),
                "revision": compute.get("revision"),
                "global": True,
            },
        }


def compute_deadline(compute: dict, at: datetime) -> str | None:
    deadline = compute.get("idle_deadline")
    return deadline if isinstance(deadline, str) else None


def expires_in(seconds: float, at: datetime) -> datetime:
    return utc(at) + timedelta(seconds=seconds)
