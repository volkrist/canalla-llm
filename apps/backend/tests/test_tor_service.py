"""The Tor service: always ready, proven, recoverable — and never quietly clearnet.

Every case here is deterministic: the SOCKS listener, the Tor binary, the launcher and the
circuit proof are fakes, so no test starts Tor, opens a socket or reaches the network. The real
service was proven separately against the installed Tor Browser bundle.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import pytest

from app.config import Settings
from app.tools.tor import service as tor_service
from app.tools.tor.service import TorService

PORTS = {"9050", "9150"}


def make_settings(tmp_path, **overrides) -> Settings:
    values = {
        "alex_llm_data_dir": str(tmp_path),
        "jwt_secret": "tor-service-test-secret-" + "s" * 32,
        "tor_managed_enabled": True,
        "tor_socks_port": 9050,
        "tor_startup_timeout_seconds": 10.0,
        "tor_supervise_seconds": 5.0,
        "tor_proof_ttl_seconds": 900,
    }
    values.update(overrides)
    return Settings(**values)


class FakeTransport:
    """Answers the Tor check the way a working exit, a clearnet exit or a broken one would."""

    def __init__(self, *, is_tor: bool = True, status: int = 200, text: str | None = None):
        self.is_tor = is_tor
        self.status = status
        self.text = text
        self.calls = 0

    async def fetch(self, url: str, *, timeout=45) -> dict:
        self.calls += 1
        body = self.text if self.text is not None else json.dumps({"IsTor": self.is_tor, "IP": "203.0.113.9"})
        return {"url": url, "status": self.status, "text": body}


class FakeProcess:
    """A stand-in for the managed Tor process."""

    def __init__(self, *, exit_code: int | None = None):
        self.exit_code = exit_code
        self.terminated = 0

    def poll(self):
        return self.exit_code

    def terminate(self):
        self.terminated += 1


@pytest.fixture
def listener(monkeypatch):
    """Control which loopback ports answer, without opening a socket."""
    state = {"open": set()}

    def fake_socks_listening(host, port, timeout=0.4):
        assert host in {"127.0.0.1", "::1", "localhost"}, "Tor must stay loopback-only"
        return str(port) in state["open"]

    monkeypatch.setattr(tor_service, "socks_listening", fake_socks_listening)
    return state


@pytest.fixture
def binary(monkeypatch):
    """A discovered Tor binary path, without one existing on disk."""
    state = {"path": None}

    def fake_find(settings):
        return state["path"]

    monkeypatch.setattr(tor_service, "find_tor_binary", fake_find)
    return state


def build(tmp_path, *, transport=None, spawn=None, clock=None, **overrides) -> TorService:
    transport = transport or FakeTransport()
    return TorService(
        make_settings(tmp_path, **overrides),
        clock=clock,
        spawn=spawn,
        transport_factory=lambda host, port: transport,
    )


def run(coro):
    return asyncio.run(coro)


def write_log(tmp_path, line: str) -> None:
    logs = tmp_path / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "tor.log").write_text(line + "\n", encoding="utf-8")


# ------------------------------------------------------------------ discovery and readiness


def test_a_proven_endpoint_is_ready_and_reported_as_external(tmp_path, listener, binary):
    listener["open"] = {"9050"}
    service = build(tmp_path)

    snapshot = run(service.ensure(reason="startup"))

    assert snapshot["state"] == "ready"
    assert snapshot["verified_chain"] is True
    assert snapshot["listening"] is True
    assert snapshot["managed"] is False  # somebody else's Tor: used, never claimed
    assert snapshot["endpoint"] == {"host": "127.0.0.1", "port": 9050}
    assert snapshot["reason"] is None
    assert snapshot["fallback"] == "none"
    proof = json.loads((tmp_path / "runtime" / "tor.json").read_text(encoding="utf-8"))
    assert proof["verified"] is True
    assert proof["method"] == "socks5h"
    # The exit address is never stored; only the fact of a verified route is.
    assert set(proof) == {"verified", "verified_at", "host", "port", "source", "method"}


def test_a_second_port_is_used_when_the_first_is_silent(tmp_path, listener, binary):
    listener["open"] = {"9150"}
    service = build(tmp_path)

    snapshot = run(service.ensure(reason="startup"))

    assert snapshot["state"] == "ready"
    assert snapshot["endpoint"] == {"host": "127.0.0.1", "port": 9150}
    assert snapshot["candidates"] == [9050, 9150]


def test_a_listening_port_without_a_tor_exit_is_never_ready(tmp_path, listener, binary):
    listener["open"] = {"9050"}
    service = build(tmp_path, transport=FakeTransport(is_tor=False))

    snapshot = run(service.ensure(reason="startup"))

    assert snapshot["state"] == "unavailable"
    assert snapshot["reason"] == "tor_circuit_invalid"
    assert snapshot["verified_chain"] is False
    assert not (tmp_path / "runtime" / "tor.json").exists()


def test_a_bootstrapping_tor_is_starting_not_broken(tmp_path, listener, binary):
    listener["open"] = {"9050"}
    write_log(tmp_path, "Sep 22 22:34:17.000 [notice] Bootstrapped 68% (loading_descriptors): Loading")
    service = build(tmp_path, transport=FakeTransport(is_tor=False))

    snapshot = run(service.ensure(reason="startup"))

    # Not ready, but not a failure either: Tor's own bootstrap is the authority on progress.
    assert snapshot["state"] == "reconnecting"
    assert snapshot["reason"] is None
    assert snapshot["verified_chain"] is False


def test_a_finished_bootstrap_with_a_failing_proof_is_a_circuit_problem(tmp_path, listener, binary):
    listener["open"] = {"9050"}
    write_log(tmp_path, "Sep 22 22:34:17.000 [notice] Bootstrapped 100% (done): Done")
    service = build(tmp_path, transport=FakeTransport(is_tor=False))

    snapshot = run(service.ensure(reason="startup"))

    assert snapshot["state"] == "unavailable"
    assert snapshot["reason"] == "tor_circuit_invalid"


def test_no_endpoint_and_no_binary_is_an_honest_missing_dependency(tmp_path, listener, binary):
    spawns = []
    binary["path"] = None
    service = build(tmp_path, spawn=lambda *args: spawns.append(args) or FakeProcess())

    snapshot = run(service.ensure(reason="startup"))

    assert snapshot["state"] == "unavailable"
    assert snapshot["reason"] == "tor_not_installed"
    assert spawns == []  # nothing to start is not a reason to start something
    assert snapshot["binary"] is None


def test_a_route_that_failed_recovers_to_ready(tmp_path, listener, binary):
    """«Tor восстанавливается» → «Готово» happens by itself: the next proof through the same
    endpoint is readiness. No restart, no second process and no manual action."""
    listener["open"] = {"9050"}
    write_log(tmp_path, "Sep 22 22:34:17.000 [notice] Bootstrapped 100% (done): Done")
    exits = {"current": FakeTransport(is_tor=False)}
    service = TorService(
        make_settings(tmp_path),
        transport_factory=lambda host, port: exits["current"],
    )

    broken = run(service.ensure(reason="startup"))
    assert broken["state"] == "unavailable"
    assert broken["reason"] == "tor_circuit_invalid"

    # The exit answers as Tor again; the supervisor's next pass is what turns the chip green.
    exits["current"] = FakeTransport(is_tor=True)
    recovered = run(service.ensure(reason="supervise"))

    assert recovered["state"] == "ready"
    assert recovered["verified_chain"] is True
    assert recovered["reason"] is None
    assert recovered["endpoint"] == {"host": "127.0.0.1", "port": 9050}


def test_a_restart_reproves_the_saved_endpoint_without_a_second_tor(tmp_path, listener, binary):
    """The app-restart path: a new service over the same data root uses the endpoint it proved
    before, never starts a second Tor, and never treats the stored proof as readiness on its own."""
    listener["open"] = {"9050"}
    binary["path"] = ("C:/Tor/tor.exe", "tor_browser")
    spawns = []
    first = build(tmp_path, spawn=lambda *args: spawns.append(args) or FakeProcess())
    assert run(first.ensure(reason="startup"))["state"] == "ready"
    assert spawns == []  # an endpoint that answers is used, never duplicated

    transport = FakeTransport()
    second = build(
        tmp_path,
        transport=transport,
        spawn=lambda *args: spawns.append(args) or FakeProcess(),
    )
    snapshot = run(second.ensure(reason="startup"))

    assert snapshot["state"] == "ready"
    assert snapshot["endpoint"] == {"host": "127.0.0.1", "port": 9050}
    assert snapshot["verified_chain"] is True
    assert spawns == []  # nothing was started on the way back
    assert transport.calls == 1  # the route is re-proven, not assumed from the file


# ---------------------------------------------------------------------------- managed process


def test_a_managed_process_starts_when_nothing_answers(tmp_path, listener, binary):
    binary["path"] = ("C:/Tor/tor.exe", "tor_browser")
    spawns = []

    def spawn(path, port, data_dir, log_path):
        spawns.append((path, port))
        listener["open"] = {str(port)}  # the process opens its SOCKS listener
        return FakeProcess()

    service = build(tmp_path, spawn=spawn)

    snapshot = run(service.ensure(reason="startup"))

    assert snapshot["state"] == "ready"
    assert snapshot["managed"] is True
    assert spawns == [(("C:/Tor/tor.exe", "tor_browser")[0], 9050)]
    assert snapshot["binary"] == {"path": "C:/Tor/tor.exe", "source": "tor_browser"}


def test_a_dead_managed_process_is_restarted(tmp_path, listener, binary):
    binary["path"] = ("C:/Tor/tor.exe", "tor_browser")
    spawns = []

    def spawn(path, port, data_dir, log_path):
        spawns.append(port)
        dead = len(spawns) == 1
        # A dead process does not keep its listener open; the restart is what reopens it.
        listener["open"] = set() if dead else {str(port)}
        return FakeProcess(exit_code=1) if dead else FakeProcess()

    service = build(tmp_path, spawn=spawn)

    first = run(service.ensure(reason="startup"))
    assert first["state"] == "unavailable"
    assert first["reason"] == "tor_no_endpoint"

    second = run(service.ensure(reason="supervise"))

    assert len(spawns) == 2, "recovery means a restart, not a red chip"
    assert second["state"] == "ready"
    assert second["managed"] is True


def test_managed_tor_is_never_started_when_it_is_disabled(tmp_path, listener, binary):
    binary["path"] = ("C:/Tor/tor.exe", "tor_browser")
    spawns = []
    service = build(
        tmp_path, spawn=lambda *args: spawns.append(args) or FakeProcess(), tor_managed_enabled=False
    )

    snapshot = run(service.ensure(reason="startup"))

    assert snapshot["state"] == "unavailable"
    assert snapshot["reason"] == "tor_managed_disabled"
    assert spawns == []


def test_a_binary_that_is_present_but_cannot_start_is_reported(tmp_path, listener, binary):
    binary["path"] = ("C:/Tor/tor.exe", "standalone")

    def spawn(*_args):
        raise OSError("cannot execute")

    service = build(tmp_path, spawn=spawn)

    snapshot = run(service.ensure(reason="startup"))

    assert snapshot["state"] == "unavailable"
    assert snapshot["reason"] == "tor_start_failed"


# ------------------------------------------------------------------------------- proof rules


def test_a_proof_only_vouches_for_the_endpoint_it_came_from(tmp_path, listener, binary):
    listener["open"] = {"9050"}
    service = build(tmp_path)
    run(service.ensure(reason="startup"))
    assert service.verified() is True

    # The endpoint moves (the old one is gone): the stored proof must not vouch for the new one.
    listener["open"] = {"9150"}
    service._endpoint = ("127.0.0.1", 9150)  # type: ignore[assignment]  # simulate a moved endpoint

    assert service.proof_endpoint_matches() is False
    assert service.verified() is False


def test_an_expired_proof_is_not_readiness(tmp_path, listener, binary):
    listener["open"] = {"9050"}
    now = {"value": tor_service.datetime(2026, 9, 22, 12, 0, tzinfo=tor_service.timezone.utc)}
    service = build(tmp_path, clock=lambda: now["value"])
    run(service.ensure(reason="startup"))
    assert service.snapshot()["state"] == "ready"

    now["value"] = now["value"] + timedelta(seconds=901)

    snapshot = service.snapshot()
    assert snapshot["state"] != "ready"
    assert snapshot["verified_chain"] is False
    assert snapshot["proof_age_seconds"] == 901


def test_a_snapshot_never_calls_the_network_or_starts_anything(tmp_path, listener, binary):
    binary["path"] = None
    service = TorService(
        make_settings(tmp_path),
        transport_factory=lambda host, port: pytest.fail("the status path must not fetch anything"),
        spawn=lambda *args: pytest.fail("the status path must not start a process"),
    )

    snapshot = service.snapshot()

    assert snapshot["state"] == "starting"  # before the first attempt: nothing claimed yet
    assert snapshot["verified_chain"] is False
    assert snapshot["fallback"] == "none"
    assert snapshot["method"] == "socks5h"


def test_the_service_stops_only_what_it_owns(tmp_path, listener, binary):
    service = build(tmp_path)
    run(service.stop())  # nothing owned: no process, no terminate

    binary["path"] = ("C:/Tor/tor.exe", "tor_browser")
    process = FakeProcess()

    def spawn(path, port, data_dir, log_path):
        listener["open"] = {str(port)}
        return process

    owned = build(tmp_path, spawn=spawn)
    run(owned.ensure(reason="startup"))
    run(owned.stop())

    assert process.terminated == 1
    assert owned.snapshot()["state"] == "unavailable"


def test_an_external_tor_is_never_terminated(tmp_path, listener, binary):
    listener["open"] = {"9050"}
    service = build(tmp_path)
    run(service.ensure(reason="startup"))

    run(service.stop())

    # We used somebody else's Tor and never claimed ownership of it.
    assert service.snapshot()["managed"] is False


def test_tools_route_through_the_endpoint_that_was_proven(tmp_path, listener, binary):
    listener["open"] = {"9150"}
    service = build(tmp_path, tor_service_active=True)
    run(service.ensure(reason="startup"))

    tor_service.set_active_service(service)
    try:
        # The providers inherit the proven endpoint instead of the configured default.
        assert tor_service.active_endpoint("127.0.0.1", 9050) == ("127.0.0.1", 9150)
    finally:
        tor_service.set_active_service(None)
    assert tor_service.active_endpoint("127.0.0.1", 9050) == ("127.0.0.1", 9050)
