"""One shared RunPod account balance snapshot for every authenticated user.

The RunPod account belongs to the installation, not to a user, so the balance is
identical for everyone and is cached once per backend process. Refreshes are
single-flight: concurrent requests and concurrent users never fan out into several
upstream calls. Nothing in this module can create, resume or stop compute.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal

from ..compute.controller import estimate, utc
from ..compute.runpod_api import ERROR_MESSAGES, RunPodError

logger = logging.getLogger(__name__)

ACTIVE_INTERVAL = 5.0
IDLE_INTERVAL = 15.0
LOW_BALANCE_USD = Decimal("5.00")


def iso(value):
    return None if value is None else utc(value).isoformat()


class RunPodBalanceService:
    def __init__(
        self,
        api,
        active=None,
        session=None,
        clock=None,
        active_interval=ACTIVE_INTERVAL,
        idle_interval=IDLE_INTERVAL,
    ):
        self.api = api
        self.active = active or (lambda: False)
        self.session = session or (lambda: None)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.active_interval = active_interval
        self.idle_interval = idle_interval
        self._lock = asyncio.Lock()
        self._task = None
        self._balance = None
        self._spend = None
        self._fetched_at = None
        self._last_success_at = None
        self._error_code = None
        self._message = None
        self._logged = None
        self.fetches = 0  # upstream call counter; used by tests

    @property
    def configured(self):
        return bool(getattr(self.api, "configured", False))

    def interval(self):
        try:
            active = bool(self.active())
        except Exception:
            active = False
        return self.active_interval if active else self.idle_interval

    def _age(self):
        if self._fetched_at is None:
            return None
        return (self.clock() - utc(self._fetched_at)).total_seconds()

    def _usage(self):
        try:
            row = self.session()
        except Exception:
            return None
        if row is None or not row.started_at:
            return None
        seconds, cost = estimate(row, self.clock())
        return {
            "gpu": row.gpu_type,
            "hourly_rate_usd": str(row.hourly_rate),
            "estimated_usd": str(cost),
            "billable_seconds": seconds,
            "managed": bool(row.managed),
            "started_at": iso(row.started_at),
            "budget_usd": str(row.session_budget),
        }

    def payload(self):
        available = self._balance is not None
        return {
            "configured": self.configured,
            # Money is serialized as a string: it never loses Decimal precision and it
            # cannot regress into the historical Decimal/datetime JSON failure.
            "available": available,
            "balance_usd": None if not available else str(self._balance),
            "account_spend_per_hr": None if self._spend is None else str(self._spend),
            "low": bool(available and self._balance < LOW_BALANCE_USD),
            "low_threshold_usd": str(LOW_BALANCE_USD),
            "stale": bool(available and self._error_code is not None),
            "error_code": self._error_code,
            "message": self._message,
            "fetched_at": iso(self._fetched_at),
            "last_success_at": iso(self._last_success_at),
            "refresh_seconds": self.interval(),
            "shared_account": True,
            "read_only": True,
            "active_session": self._usage(),
        }

    async def snapshot(self):
        """Cached snapshot. Refreshes at most once per interval, single-flight."""
        if not self.configured:
            self._error_code, self._message = "not_configured", None
            return self.payload()
        age = self._age()
        if age is not None and age < self.interval():
            return self.payload()
        async with self._lock:
            age = self._age()
            if age is None or age >= self.interval():
                await self._refresh()
        return self.payload()

    async def refresh(self):
        """Force one read-only upstream refresh. Used by tests and explicit recovery."""
        if not self.configured:
            self._error_code, self._message = "not_configured", None
            return self.payload()
        async with self._lock:
            await self._refresh()
        return self.payload()

    async def _refresh(self):
        self.fetches += 1
        try:
            data = await self.api.account_balance()
        except RunPodError as error:
            self._failure(error.code)
        except Exception:
            self._failure("runpod_unavailable")
        else:
            self._balance = data["balance"]
            self._spend = data.get("current_spend_per_hr")
            self._fetched_at = self.clock()
            self._last_success_at = self._fetched_at
            self._error_code = self._message = self._logged = None

    def _failure(self, code):
        # The timestamp moves on failure too, so a broken provider is polled at the
        # interval instead of once per user request. The previous balance is kept.
        self._fetched_at = self.clock()
        self._error_code = code
        self._message = ERROR_MESSAGES.get(code)
        if self._logged != code:
            self._logged = code
            logger.warning("runpod_balance_refresh_failed code=%s", code)

    async def start(self):
        """Optional background refresh so the UI does not wait on the first poll."""
        if self._task or not self.configured:
            return
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if not self._task:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.warning("runpod_balance_loop_stop_failure")
        self._task = None

    async def _loop(self):
        while True:
            try:
                await self._refresh_if_stale()
            except Exception:
                logger.warning("runpod_balance_loop_failure")
            await asyncio.sleep(self.interval())

    async def _refresh_if_stale(self):
        age = self._age()
        if age is None or age >= self.interval():
            async with self._lock:
                age = self._age()
                if age is None or age >= self.interval():
                    await self._refresh()
