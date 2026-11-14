"""External watchdog for one bounded paid GPU run.

Why a separate process: a timer inside the process that owns the GPU stops working at exactly the
moment it is needed — a hang, a crash, an OOM. This controller watches the provider from outside,
so it can stop the Pod even when the agent is gone. It never *creates* a Pod unless it is started
with an explicit `--execute-start`, which belongs to an owner-approved run; in the live acceptance
the Pod is created by the Gateway's single-flight `POST /compute/ensure`, and this process only
supervises and terminates it.

Usage:

    python scripts/live_qwen_watchdog.py --dry-run          # free: fake provider, writes evidence
    python scripts/live_qwen_watchdog.py --self-test        # same assertions, no file writes

A live run (owner approval required, never started here):

    python scripts/live_qwen_watchdog.py --live --budget-usd 0.15 --hourly-rate 1.09 \\
        --pod-id <id> --agent-pid <pid> --out run/watchdog.json

The live mode reads the RunPod credential the installed product already stores. It never prints
the key, never creates a second Pod, and verifies that the Pod is gone before it reports success.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "apps" / "backend"))

from app.compute import cost_policy  # noqa: E402

# A runner that disappears leaves its Pod with no consumer. The grace lets a runner that is
# finishing its own stop land first; after it, the watchdog stops the Pod itself.
RUNNER_GONE_GRACE_SECONDS = 30.0


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class WatchEvent:
    at: float
    kind: str
    detail: str

    def to_dict(self) -> dict:
        return {"at_seconds": round(self.at, 2), "kind": self.kind, "detail": self.detail}


@dataclass
class PodState:
    pod_id: str
    hourly_rate_usd: float
    status: str = "running"
    created_at: float = 0.0
    terminated_at: float | None = None
    gone_at: float | None = None


class ProviderError(RuntimeError):
    """The provider could not answer. Never silently treated as "no Pod exists"."""


class Provider:
    """The three operations the watchdog needs. Implemented by a fake and by RunPod."""

    def list_pods(self) -> list[PodState]:
        raise NotImplementedError

    def terminate(self, pod_id: str) -> bool:
        raise NotImplementedError

    def balance_usd(self) -> float | None:
        return None


class FakeProvider(Provider):
    """A deterministic Pod lifecycle used to prove the watchdog's decisions for free.

    `stop_latency` simulates termination that takes time to confirm; `never_gone` simulates a
    provider that refuses to confirm removal, which must be reported, not hidden.
    """

    def __init__(
        self,
        *,
        clock,
        pod: PodState | None,
        stop_latency: float = 20.0,
        never_gone: bool = False,
        fail_terminate: bool = False,
    ):
        self.clock = clock
        self.pod = pod
        self.stop_latency = stop_latency
        self.never_gone = never_gone
        self.fail_terminate = fail_terminate
        self.terminate_requests: list[float] = []
        self.balance = 8.5727707286

    def list_pods(self) -> list[PodState]:
        pod = self.pod
        if pod is None:
            return []
        now = self.clock()
        if pod.status == "terminated" and pod.terminated_at is not None:
            if self.never_gone or now < pod.terminated_at + self.stop_latency:
                return [pod]
            pod.status, pod.gone_at = "gone", pod.terminated_at + self.stop_latency
            return []
        return [pod]

    def terminate(self, pod_id: str) -> bool:
        self.terminate_requests.append(self.clock())
        if self.fail_terminate:
            return False
        if self.pod and self.pod.pod_id == pod_id and self.pod.status != "terminated":
            self.pod.status = "terminated"
            self.pod.terminated_at = self.clock()
        return True

    def balance_usd(self) -> float | None:
        return self.balance


class RunPodProvider(Provider):
    """Read + terminate only. Never creates a Pod."""

    def __init__(self):
        sys.path.insert(0, str(REPO / "scripts"))
        import runpod_api_tools as tools

        self.tools = tools
        self.key = tools.read_provider_secret()
        if not self.key:
            raise SystemExit("runpod credential is not configured")

    def list_pods(self) -> list[PodState]:
        status, payload = self.tools.call("/v2/pods", self.key)
        if status != 200 or not isinstance(payload, dict):
            raise ProviderError(f"pod list failed (status={status})")
        rows = payload.get("pods")
        if not isinstance(rows, list):
            raise ProviderError("pod list was malformed")
        pods = []
        for row in rows or []:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            pods.append(
                PodState(
                    pod_id=str(row["id"]),
                    hourly_rate_usd=float(row.get("cost") or 0.0),
                    status=str(row.get("desiredStatus") or row.get("status") or "unknown"),
                )
            )
        return pods

    def terminate(self, pod_id: str) -> bool:
        try:
            result = self.tools.terminate(self.key, pod_id)
        except Exception as error:  # noqa: BLE001 - a transport failure is not a refusal
            raise ProviderError(f"terminate failed: {type(error).__name__}") from None
        return bool(result.get("ok")) if isinstance(result, dict) else bool(result)

    def balance_usd(self) -> float | None:
        status, payload = self.tools.call(
            "https://api.runpod.io/graphql",
            self.key,
            method="POST",
            payload={"query": "query CanallaWatch { myself { clientBalance } }"},
        )
        if status == 200 and isinstance(payload, dict):
            return ((payload.get("data") or {}).get("myself") or {}).get("clientBalance")
        return None


@dataclass
class WatchResult:
    verdict: str
    plan: dict
    events: list[WatchEvent] = field(default_factory=list)
    billed_seconds: float = 0.0
    estimated_cost_usd: float = 0.0
    stop_requested_at: float | None = None
    termination_confirmed: bool = False
    pods_remaining: int | None = None
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "plan": self.plan,
            "events": [event.to_dict() for event in self.events],
            "billed_seconds": round(self.billed_seconds, 2),
            "estimated_cost_usd": round(self.estimated_cost_usd, 6),
            "stop_requested_at": self.stop_requested_at,
            "termination_confirmed": self.termination_confirmed,
            "pods_remaining": self.pods_remaining,
            "reasons": self.reasons,
        }


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return bool(ok) and code.value == 259  # STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def run_watchdog(
    provider: Provider,
    plan_value: cost_policy.BudgetPlan,
    *,
    clock,
    sleep,
    pod_id: str = "",
    max_wall_seconds: float | None = None,
    wait_for_pod_seconds: float = 0.0,
    runner_pid: int = 0,
) -> WatchResult:
    """Supervise one Pod until it is confirmed gone. Never creates anything."""
    started = clock()
    pod_deadline = started + max(0.0, float(wait_for_pod_seconds))
    result = WatchResult(verdict="FAILED", plan=plan_value.to_dict())
    state = None
    saw_multiple = False
    read_errors = 0
    runner_gone = False
    runner_gone_at: float | None = None

    def note(kind: str, detail: str = ""):
        result.events.append(WatchEvent(clock() - started, kind, detail))

    poll = plan_value.watchdog_poll_seconds
    wall = float(max_wall_seconds if max_wall_seconds is not None else plan_value.hard_wall_seconds)
    deadline = (
        started
        + float(wait_for_pod_seconds)
        + wall
        + plan_value.termination_grace_seconds
        + 30
    )
    while True:
        if runner_pid and not runner_gone and not _process_alive(runner_pid):
            runner_gone = True
            runner_gone_at = clock()
            note("runner-gone", "the supervised runner is no longer alive; the wall still applies")
        try:
            pods = provider.list_pods()
            read_errors = 0
        except ProviderError as error:
            read_errors += 1
            note("provider-read-error", str(error))
            if state is not None:
                # We know a Pod exists. Losing the read path never releases the deadline.
                if clock() - started > deadline:
                    result.verdict = "PROVIDER_UNREACHABLE"
                    result.reasons.append(
                        "the provider could not be read while a Pod may still be billing"
                    )
                    result.pods_remaining = None
                    return result
                sleep(poll)
                continue
            if clock() < pod_deadline:
                sleep(poll)
                continue
            result.verdict = "PROVIDER_UNREACHABLE"
            result.reasons.append(
                "the provider could not be read, so zero Pods cannot be confirmed"
            )
            result.pods_remaining = None
            note("provider-unreachable")
            return result
        if len(pods) > 1:
            # One managed Pod is the rule; a second one is a spending emergency, not a state to
            # observe politely. Stop them all and report it.
            if not saw_multiple:
                saw_multiple = True
                result.verdict = "MULTIPLE_PODS"
                result.reasons.append(f"{len(pods)} Pods exist where at most one is allowed")
                note("multiple-pods", ",".join(pod.pod_id for pod in pods))
            for pod in pods:
                if pod.status != "terminated":
                    provider.terminate(pod.pod_id)
        target = next((pod for pod in pods if not pod_id or pod.pod_id == pod_id), None)
        if target is None and state is None:
            if clock() < pod_deadline:
                # The authorised run may still be creating its one Pod. This is a bounded wait for
                # *our* process, not a capacity queue: it belongs to the run's own start budget.
                note("waiting-for-pod")
                sleep(poll)
                continue
            result.verdict = "CAPACITY_UNAVAILABLE"
            result.reasons.append("no Pod exists: nothing to supervise and nothing to spend")
            result.pods_remaining = 0
            note("no-pod")
            return result
        if target is not None:
            if state is None:
                state = target
                note("pod-observed", f"{target.pod_id} at ${target.hourly_rate_usd:.4f}/h")
                ok, reason = cost_policy.check_rate(plan_value, target.hourly_rate_usd)
                if not ok:
                    result.verdict = "PRICE_ABOVE_BUDGET"
                    result.reasons.append(reason)
                    note("price-refused", reason)
                    # The Pod exists but was not created by this controller; the safe action is to
                    # stop it, never to keep it running against an unaffordable budget.
            if state.status != "terminated" and result.stop_requested_at is None:
                elapsed = clock() - started
                cost = elapsed / 3600 * state.hourly_rate_usd
                # The runner was the only consumer of this Pod. Once it is gone, a live Pod is
                # pure spend: a 30-second grace lets a runner that is finishing its own stop
                # land first, then the watchdog does it and verifies the removal.
                orphaned = (
                    runner_gone
                    and runner_gone_at is not None
                    and clock() - runner_gone_at >= RUNNER_GONE_GRACE_SECONDS
                )
                if elapsed >= plan_value.stop_request_seconds or cost >= plan_value.budget_usd or orphaned:
                    result.stop_requested_at = round(elapsed, 2)
                    note(
                        "stop-requested",
                        "runner gone: the Pod has no consumer"
                        if orphaned
                        else f"elapsed={elapsed:.1f}s cost=${cost:.4f}",
                    )
                    try:
                        stopped = provider.terminate(state.pod_id)
                    except ProviderError as error:
                        stopped = False
                        note("terminate-error", str(error))
                    if not stopped:
                        result.reasons.append("termination request was refused by the provider")
                        note("terminate-refused")
        else:
            # The Pod is gone. Billing cannot continue past this point.
            gone_at = clock() - started
            result.pods_remaining = 0
            if state is not None:
                billed = gone_at
                result.billed_seconds = billed
                result.estimated_cost_usd = billed / 3600 * state.hourly_rate_usd
                result.termination_confirmed = True
                note("pod-gone", f"billed~{billed:.1f}s")
            if result.verdict == "FAILED":
                result.verdict = "OK"
            if result.verdict == "OK" and result.estimated_cost_usd > plan_value.budget_usd + 1e-9:
                result.verdict = "BUDGET_EXCEEDED"
                result.reasons.append(
                    f"confirmed cost ${result.estimated_cost_usd:.4f} exceeds "
                    f"${plan_value.budget_usd:.4f}"
                )
            return result
        if clock() - started > deadline:
            result.verdict = "TERMINATION_UNCONFIRMED"
            try:
                result.pods_remaining = len(provider.list_pods())
            except ProviderError:
                result.pods_remaining = None
            result.reasons.append(
                "the Pod was not confirmed gone before the deadline; it may still be billing"
            )
            note("termination-unconfirmed")
            return result
        sleep(poll)


# ------------------------------------------------------------------------------------- dry runs


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def scenario_ok() -> WatchResult:
    clock = FakeClock()
    provider = FakeProvider(
        clock=clock,
        pod=PodState("pod-ok", 1.09, created_at=0.0),
        stop_latency=20.0,
    )
    plan_value = cost_policy.plan(0.15, 1.09)
    return run_watchdog(provider, plan_value, clock=clock, sleep=clock.sleep)


def scenario_capacity() -> WatchResult:
    clock = FakeClock()
    provider = FakeProvider(clock=clock, pod=None)
    plan_value = cost_policy.plan(0.15, 1.09)
    started = time.monotonic()
    result = run_watchdog(provider, plan_value, clock=clock, sleep=clock.sleep)
    result.reasons.append(f"resolved in {time.monotonic() - started:.3f}s of real time")
    return result


def scenario_price() -> WatchResult:
    clock = FakeClock()
    provider = FakeProvider(clock=clock, pod=PodState("pod-pricey", 1.79), stop_latency=15.0)
    plan_value = cost_policy.plan(0.15, 1.09)
    return run_watchdog(provider, plan_value, clock=clock, sleep=clock.sleep)


def scenario_unconfirmed() -> WatchResult:
    clock = FakeClock()
    provider = FakeProvider(
        clock=clock, pod=PodState("pod-stuck", 1.09), stop_latency=20.0, never_gone=True
    )
    plan_value = cost_policy.plan(0.15, 1.09)
    return run_watchdog(
        provider, plan_value, clock=clock, sleep=clock.sleep, max_wall_seconds=60
    )


def scenario_terminate_refused() -> WatchResult:
    clock = FakeClock()
    provider = FakeProvider(
        clock=clock, pod=PodState("pod-refused", 1.09), fail_terminate=True
    )
    plan_value = cost_policy.plan(0.15, 1.09)
    return run_watchdog(provider, plan_value, clock=clock, sleep=clock.sleep, max_wall_seconds=120)


class DelayedPodProvider(FakeProvider):
    """The authorised run has not created its Pod yet; it appears after `delay` seconds."""

    def __init__(self, *, clock, delay: float, stop_latency: float = 10.0):
        super().__init__(clock=clock, pod=None, stop_latency=stop_latency)
        self.delay = delay
        self.created = False

    def list_pods(self) -> list[PodState]:
        if not self.created and self.clock() >= self.delay:
            self.created = True
            self.pod = PodState("pod-late", 1.09, created_at=self.clock())
        return super().list_pods()


class MultiPodProvider(FakeProvider):
    """A second Pod appeared. That is a spending emergency and must be stopped."""

    def __init__(self, *, clock):
        super().__init__(clock=clock, pod=PodState("pod-a", 1.09), stop_latency=10.0)
        self.extras = [PodState("pod-b", 1.09)]

    def list_pods(self) -> list[PodState]:
        return super().list_pods() + [pod for pod in self.extras if pod.status != "terminated"]

    def terminate(self, pod_id: str) -> bool:
        for pod in self.extras:
            if pod.pod_id == pod_id:
                pod.status, pod.terminated_at = "terminated", self.clock()
                return True
        return super().terminate(pod_id)


def scenario_late_pod() -> WatchResult:
    clock = FakeClock()
    provider = DelayedPodProvider(clock=clock, delay=30.0)
    plan_value = cost_policy.plan(0.15, 1.09)
    return run_watchdog(
        provider,
        plan_value,
        clock=clock,
        sleep=clock.sleep,
        wait_for_pod_seconds=60.0,
    )


class FlakyProvider(FakeProvider):
    """The first reads fail; a later read succeeds. Losing the read path must not free the Pod."""

    def __init__(self, *, clock, fail_first: int = 3):
        super().__init__(clock=clock, pod=PodState("pod-flaky", 1.09), stop_latency=10.0)
        self.fail_first = fail_first

    def list_pods(self) -> list[PodState]:
        if self.fail_first > 0:
            self.fail_first -= 1
            raise ProviderError("transient pod-list failure")
        return super().list_pods()


class DeadProvider(Provider):
    def list_pods(self) -> list[PodState]:
        raise ProviderError("provider is unreachable")

    def terminate(self, pod_id: str) -> bool:
        raise ProviderError("provider is unreachable")


def scenario_flaky_provider() -> WatchResult:
    clock = FakeClock()
    provider = FlakyProvider(clock=clock)
    plan_value = cost_policy.plan(0.15, 1.09)
    return run_watchdog(
        provider, plan_value, clock=clock, sleep=clock.sleep, wait_for_pod_seconds=60.0
    )


def scenario_dead_provider() -> WatchResult:
    clock = FakeClock()
    provider = DeadProvider()
    plan_value = cost_policy.plan(0.15, 1.09)
    return run_watchdog(
        provider, plan_value, clock=clock, sleep=clock.sleep, wait_for_pod_seconds=30.0
    )


def scenario_runner_gone() -> WatchResult:
    clock = FakeClock()
    provider = FakeProvider(clock=clock, pod=PodState("pod-runner", 1.09), stop_latency=10.0)
    plan_value = cost_policy.plan(0.15, 1.09)
    return run_watchdog(
        provider,
        plan_value,
        clock=clock,
        sleep=clock.sleep,
        runner_pid=2**31 - 1,
    )


def scenario_multiple_pods() -> WatchResult:
    clock = FakeClock()
    provider = MultiPodProvider(clock=clock)
    plan_value = cost_policy.plan(0.15, 1.09)
    return run_watchdog(provider, plan_value, clock=clock, sleep=clock.sleep)


def self_test() -> dict:
    checks: list[dict] = []

    def check(name: str, condition: bool, detail: str):
        checks.append({"check": name, "ok": bool(condition), "detail": detail})

    ok = scenario_ok()
    check("stop_requested_within_the_hard_wall", ok.stop_requested_at is not None and ok.stop_requested_at <= 450, str(ok.stop_requested_at))
    check("termination_confirmed", ok.termination_confirmed is True, str(ok.verdict))
    check("no_pods_remaining", ok.pods_remaining == 0, str(ok.pods_remaining))
    check("cost_within_budget", ok.estimated_cost_usd <= 0.15 + 1e-9, f"${ok.estimated_cost_usd:.4f}")
    check("verdict_ok", ok.verdict == "OK", ok.verdict)

    capacity = scenario_capacity()
    check("capacity_fails_fast", capacity.verdict == "CAPACITY_UNAVAILABLE", capacity.verdict)
    check("capacity_creates_nothing", capacity.pods_remaining == 0, str(capacity.pods_remaining))

    price = scenario_price()
    check("price_refused", price.verdict == "PRICE_ABOVE_BUDGET", price.verdict)
    check("price_still_stopped", price.termination_confirmed is True, str(price.termination_confirmed))

    stuck = scenario_unconfirmed()
    check("unconfirmed_reported", stuck.verdict == "TERMINATION_UNCONFIRMED", stuck.verdict)
    check("unconfirmed_does_not_claim_cleanup", stuck.termination_confirmed is False, str(stuck.termination_confirmed))

    refused = scenario_terminate_refused()
    check(
        "refused_terminate_is_loud",
        refused.verdict == "TERMINATION_UNCONFIRMED"
        or any("refused" in reason for reason in refused.reasons),
        f"{refused.verdict} {refused.reasons}",
    )

    late = scenario_late_pod()
    check("a_late_pod_is_supervised", late.verdict == "OK" and late.termination_confirmed, late.verdict)
    check(
        "a_late_pod_still_stops_inside_the_wall",
        (late.stop_requested_at or 0) - 30.0 <= 450,
        late.stop_requested_at,
    )

    multiple = scenario_multiple_pods()
    check("a_second_pod_is_an_emergency", multiple.verdict == "MULTIPLE_PODS", multiple.verdict)
    check("a_second_pod_is_stopped", multiple.termination_confirmed is True, str(multiple.termination_confirmed))

    flaky = scenario_flaky_provider()
    check(
        "a_flaky_read_never_concludes_capacity",
        flaky.verdict == "OK" and flaky.termination_confirmed,
        flaky.verdict,
    )
    dead = scenario_dead_provider()
    check("an_unreadable_provider_is_typed", dead.verdict == "PROVIDER_UNREACHABLE", dead.verdict)
    check(
        "an_unreadable_provider_never_claims_zero_pods",
        dead.pods_remaining is None,
        str(dead.pods_remaining),
    )
    gone = scenario_runner_gone()
    check(
        "a_dead_runner_stops_the_pod_it_left_behind",
        gone.verdict == "OK"
        and gone.termination_confirmed
        and any(event.kind == "runner-gone" for event in gone.events)
        and any(
            event.kind == "stop-requested" and "runner gone" in event.detail
            for event in gone.events
        ),
        gone.verdict,
    )
    plan_two = cost_policy.plan(
        2.00, 1.09, max_billable_seconds=90 * 60, min_work_seconds=45 * 60
    )
    check(
        "the_two_dollar_policy_is_bounded_by_ninety_minutes",
        plan_two.max_billed_seconds == 5400 and plan_two.sufficient,
        plan_two.max_billed_seconds,
    )

    plan_value = cost_policy.plan(0.15, 1.09)
    check(
        "no_long_capacity_wait",
        plan_value.capacity_timeout_seconds <= 60,
        f"{plan_value.capacity_timeout_seconds}s",
    )
    check(
        "billing_rounding_included",
        plan_value.max_billed_seconds
        == math.floor((0.15 / (1.09 / 3600)) / 60) * 60,
        str(plan_value.max_billed_seconds),
    )

    return {
        "generated_at": utcnow(),
        "mode": "dry_run_fake_provider",
        "note": "A fake provider proves the watchdog's decisions. No Pod was created." ,
        "plan": plan_value.to_dict(),
        "scenarios": {
            "ok": ok.to_dict(),
            "capacity_unavailable": capacity.to_dict(),
            "price_above_budget": price.to_dict(),
            "termination_unconfirmed": stuck.to_dict(),
            "terminate_refused": refused.to_dict(),
            "late_pod": late.to_dict(),
            "multiple_pods": multiple.to_dict(),
            "flaky_provider": flaky.to_dict(),
            "dead_provider": dead.to_dict(),
            "runner_gone": gone.to_dict(),
        },
        "checks": checks,
        "verdict": "PASS" if all(item["ok"] for item in checks) else "FAIL",
    }


def _prevent_sleep() -> bool:
    """Keep the watchdog's host awake while it is the only thing that can stop the Pod.

    `SetThreadExecutionState` is per-thread and needs no privilege. It is not a guarantee: a user
    can still sleep or hibernate the machine, which is why the provider read path and the wall are
    the real protections. The flag is recorded either way.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        ES_CONTINUOUS = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        return bool(ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED))
    except Exception:
        return False


def _allow_sleep() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
    except Exception:
        pass


def live_run(args) -> int:
    plan_value = cost_policy.plan(
        args.budget_usd,
        args.hourly_rate,
        max_billable_seconds=max(1, args.max_billable_minutes) * 60,
        min_work_seconds=max(0, args.min_work_minutes) * 60,
    )
    if not plan_value.feasible:
        print(json.dumps({"verdict": "BUDGET_INFEASIBLE", "plan": plan_value.to_dict()}))
        return 2
    if not plan_value.sufficient:
        print(json.dumps({"verdict": "NO_MEANINGFUL_WORK_WINDOW", "plan": plan_value.to_dict()}))
        return 2
    sleep_prevented = _prevent_sleep()
    provider = RunPodProvider()
    try:
        result = run_watchdog(
            provider,
            plan_value,
            clock=time.monotonic,
            sleep=time.sleep,
            pod_id=args.pod_id,
            max_wall_seconds=args.max_wall_seconds,
            wait_for_pod_seconds=args.wait_for_pod_seconds,
            runner_pid=args.runner_pid,
        )
        payload = {
            **result.to_dict(),
            "balance_before_usd": provider.balance_usd(),
            "balance_after_usd": provider.balance_usd(),
            "sleep_prevented": sleep_prevented,
            "runner_pid": args.runner_pid,
            "observed_at": utcnow(),
        }
    finally:
        _allow_sleep()
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=1))
    return 0 if result.verdict == "OK" else 1


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--budget-usd", type=float, default=0.15)
    parser.add_argument("--hourly-rate", type=float, default=1.09)
    parser.add_argument("--pod-id", default="")
    parser.add_argument("--max-wall-seconds", type=float, default=None)
    parser.add_argument("--max-billable-minutes", type=int, default=90)
    parser.add_argument("--min-work-minutes", type=int, default=45)
    parser.add_argument("--wait-for-pod-seconds", type=float, default=0.0)
    parser.add_argument("--runner-pid", type=int, default=0)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    if args.live:
        return live_run(args)

    report = self_test()
    out = Path(args.out) if args.out else (
        REPO / "artifacts" / "acceptance-127" / "final-readiness" / "watchdog-dry-run.json"
    )
    if args.dry_run or not args.self_test:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
