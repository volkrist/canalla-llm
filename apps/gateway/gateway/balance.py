"""One shared RunPod account balance for every installation.

The account belongs to the Alex deployment, not to an installation or a user, so the
snapshot is cached once per gateway process and one upstream read serves everyone.
Refreshes are single-flight: 100 clients polling the Gateway never become 100 RunPod
calls. Nothing here can create, resume or stop compute, and a failed read never becomes
a fake ``$0`` — the last successful balance is kept and marked stale.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal

logger = logging.getLogger(__name__)

LOW_BALANCE_USD = Decimal("5.00")


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def iso(value: datetime | None) -> str | None:
    return None if value is None else utc(value).isoformat()


class SharedBalance:
    def __init__(
        self,
        api,
        *,
        active=None,
        clock=None,
        active_interval=5.0,
        idle_interval=15.0,
        messages=None,
    ):
        self.api = api
        self.active = active or (lambda: False)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.active_interval = float(active_interval)
        self.idle_interval = float(idle_interval)
        self.messages = messages or {}
        self._lock = asyncio.Lock()
        self._task = None
        self._balance: Decimal | None = None
        self._spend: Decimal | None = None
        self._fetched_at: datetime | None = None
        self._last_success_at: datetime | None = None
        self._error_code: str | None = None
        self._logged: str | None = None
        self.fetches = 0  # upstream call counter; asserted by tests

    @property
    def configured(self) -> bool:
        return bool(getattr(self.api, "configured", False))

    def interval(self) -> float:
        try:
            active = bool(self.active())
        except Exception:
            active = False
        return self.active_interval if active else self.idle_interval

    def _age(self) -> float | None:
        if self._fetched_at is None:
            return None
        return (self.clock() - utc(self._fetched_at)).total_seconds()

    def payload(self) -> dict:
        available = self._balance is not None
        return {
            "configured": self.configured,
            # Money stays a string: Decimal precision never survives float JSON, and the
            # client decodes it back into Decimal.
            "available": available,
            "balance_usd": None if not available else str(self._balance),
            "account_spend_per_hr": None if self._spend is None else str(self._spend),
            "low": bool(available and self._balance < LOW_BALANCE_USD),
            "low_threshold_usd": str(LOW_BALANCE_USD),
            "stale": bool(available and self._error_code is not None),
            "error_code": self._error_code,
            "message": self.messages.get(self._error_code) if self._error_code else None,
            "fetched_at": iso(self._fetched_at),
            "last_success_at": iso(self._last_success_at),
            "refresh_seconds": self.interval(),
            "shared_account": True,
            "read_only": True,
        }

    async def snapshot(self) -> dict:
        """Cached snapshot; refreshes at most once per interval, single-flight."""
        if not self.configured:
            self._error_code = "not_configured"
            return self.payload()
        age = self._age()
        if age is None or age >= self.interval():
            async with self._lock:
                age = self._age()
                if age is None or age >= self.interval():
                    await self._refresh()
        return self.payload()

    async def refresh(self) -> dict:
        """Force one read-only upstream refresh (recovery path, tests)."""
        if not self.configured:
            self._error_code = "not_configured"
            return self.payload()
        async with self._lock:
            await self._refresh()
        return self.payload()

    async def _refresh(self) -> None:
        self.fetches += 1
        try:
            data = await self.api.account_balance()
        except Exception as error:  # provider codes are already normalized by the client
            self._failure(getattr(error, "code", "runpod_unavailable"))
        else:
            self._balance = data["balance"]
            self._spend = data.get("current_spend_per_hr")
            self._fetched_at = self.clock()
            self._last_success_at = self._fetched_at
            self._error_code = self._logged = None

    def _failure(self, code: str) -> None:
        # The timestamp moves on failure too, so a broken provider is polled once per
        # interval instead of once per client request. The last balance is preserved.
        self._fetched_at = self.clock()
        self._error_code = code
        if self._logged != code:
            self._logged = code
            logger.warning("shared_balance_refresh_failed code=%s", code)

    async def start(self) -> None:
        if self._task or not self.configured:
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
            logger.warning("shared_balance_loop_stop_failure")
        self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                age = self._age()
                if age is None or age >= self.interval():
                    async with self._lock:
                        age = self._age()
                        if age is None or age >= self.interval():
                            await self._refresh()
            except Exception:
                logger.warning("shared_balance_loop_failure")
            await asyncio.sleep(self.interval())
