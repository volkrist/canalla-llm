"""The Tor service: always ready, proven, recoverable — and never quietly clearnet.

Every case here is deterministic: the SOCKS listener, the Tor binary, the launcher and the
circuit proof are fakes, so no test starts Tor, opens a socket or reaches the network. The real
service was proven separately against the installed Tor Browser bundle.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

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
    # The exit address is never stored; only the fact of a verified route, which endpoint it is and
    # (for a managed endpoint) which process serves it.
    assert set(proof) == {
        "verified",
        "verified_at",
        "host",
        "port",
        "source",
        "method",
        "pid",
        "tor_version",
    }
    assert proof["source"] == "external"
    assert proof["pid"] is None  # somebody else's process: we never claim it


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


def test_a_managed_process_starts_when_nothing_answers(tmp_path, listener, ports, binary):
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


# -------------------------------------------------------------------- the runtime Canalla ships


def stage_runtime(tmp_path, *, with_binary: bool = True) -> Path:
    """A staged bundled runtime: the daemon the installer ships plus the fetch step's metadata."""
    runtime = tmp_path / "install" / "runtime" / "tor"
    runtime.mkdir(parents=True, exist_ok=True)
    if with_binary:
        (runtime / "tor.exe").write_bytes(b"a staged daemon, not a real one")
        (runtime / "geoip").write_text("geoip", encoding="utf-8")
        (runtime / "geoip6").write_text("geoip6", encoding="utf-8")
    (runtime / "runtime.json").write_text(
        json.dumps(
            {
                "product": "tor",
                "version": "15.0.23",
                "daemon_version": "0.4.9.12",
                "license": "GPL-3.0",
                "source": "https://dist.torproject.org/torbrowser/15.0.23/",
                "binary": "tor.exe",
                "files": {"tor.exe": "0" * 64},
            }
        ),
        encoding="utf-8",
    )
    return runtime


@pytest.fixture
def ports(monkeypatch, listener):
    """Port availability follows the listener fixture, so a port choice never depends on the host."""

    def fake_available(host, port):
        return str(port) not in listener["open"]

    monkeypatch.setattr(tor_service, "port_available", fake_available)
    return fake_available


def bundled_service(tmp_path, runtime, *, transport=None, spawn=None, **overrides):
    values = {"alex_tor_runtime_dir": str(runtime), "tor_startup_timeout_seconds": 10.0}
    values.update(overrides)
    return TorService(
        make_settings(tmp_path, **values),
        spawn=spawn,
        transport_factory=transport or (lambda host, port: FakeTransport()),
    )


def test_the_bundled_daemon_is_preferred_over_anything_the_machine_has(tmp_path, monkeypatch):
    runtime = stage_runtime(tmp_path)
    monkeypatch.setattr(tor_service, "tor_binary_from_browser", lambda: Path("C:/Tor Browser/tor.exe"))
    settings = make_settings(tmp_path, alex_tor_runtime_dir=str(runtime))

    assert tor_service.bundled_tor_binary(settings) == (runtime / "tor.exe", "bundled")
    assert tor_service.find_tor_binary(settings) == (runtime / "tor.exe", "bundled")


def test_an_explicit_override_still_wins_over_the_bundle(tmp_path, monkeypatch):
    runtime = stage_runtime(tmp_path)
    mine = tmp_path / "my-own-tor.exe"
    mine.write_bytes(b"chosen by the operator")
    settings = make_settings(tmp_path, alex_tor_runtime_dir=str(runtime), tor_binary_path=str(mine))

    assert tor_service.find_tor_binary(settings) == (mine, "configured")


def test_a_runtime_directory_without_a_daemon_is_not_a_runtime(tmp_path):
    runtime = stage_runtime(tmp_path, with_binary=False)
    settings = make_settings(tmp_path, alex_tor_runtime_dir=str(runtime))

    assert tor_service.bundled_runtime_dir(settings) == runtime
    assert tor_service.bundled_tor_binary(settings) is None


def test_a_packaged_backend_finds_the_runtime_next_to_its_own_executable(tmp_path, monkeypatch):
    runtime = stage_runtime(tmp_path)
    install = runtime.parent.parent
    monkeypatch.delenv("ALEX_TOR_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("ALEX_PACKAGED", "1")
    monkeypatch.setattr(
        tor_service.sys, "executable", str(install / "sidecar" / "alex-backend" / "alex-backend.exe")
    )
    settings = make_settings(tmp_path)

    assert tor_service.bundled_runtime_dir(settings) == runtime
    assert tor_service.find_tor_binary(settings) == (runtime / "tor.exe", "bundled")


def test_a_staged_runtime_reports_its_version_and_provenance(tmp_path):
    runtime = stage_runtime(tmp_path)
    settings = make_settings(tmp_path, alex_tor_runtime_dir=str(runtime))

    metadata = tor_service.bundled_runtime_metadata(settings)
    assert metadata is not None and metadata["license"] == "GPL-3.0"
    assert tor_service.TorService(settings).runtime_version() == "0.4.9.12"
    assert TorService(settings).snapshot()["runtime_version"] == "0.4.9.12"


def test_the_bundled_runtime_becomes_ready_with_nothing_installed_by_the_user(
    tmp_path, listener, ports, monkeypatch
):
    """The acceptance the product needs: no Tor Browser, no tor.exe in PATH, no Program Files Tor."""
    runtime = stage_runtime(tmp_path)
    monkeypatch.setattr(tor_service, "tor_binary_from_browser", lambda: None)
    monkeypatch.setenv("PATH", str(tmp_path / "nothing-here"))
    spawns = []

    def spawn(path, port, data_dir, log_path):
        spawns.append((path, port))
        listener["open"] = {str(port)}
        return FakeProcess()

    snapshot = run(bundled_service(tmp_path, runtime, spawn=spawn).ensure(reason="startup"))

    assert snapshot["state"] == "ready"
    assert snapshot["managed"] is True
    assert snapshot["binary"] == {"path": str(runtime / "tor.exe"), "source": "bundled"}
    assert snapshot["runtime_version"] == "0.4.9.12"
    assert snapshot["endpoint"] == {"host": "127.0.0.1", "port": 9050}
    assert spawns == [(runtime / "tor.exe", 9050)]
    proof = json.loads((tmp_path / "runtime" / "tor.json").read_text(encoding="utf-8"))
    assert proof["source"] == "managed"
    assert proof["tor_version"] == "0.4.9.12"


def test_a_port_somebody_else_holds_is_never_taken(tmp_path, listener, ports, monkeypatch):
    runtime = stage_runtime(tmp_path)
    # A foreign endpoint that never proves itself is waited on for a moment, not for the whole window.
    monkeypatch.setattr(tor_service, "DISCOVERY_PROOF_SECONDS", 0.2)
    listener["open"] = {"9050"}  # a foreign listener that never proves itself
    spawns = []

    def spawn(path, port, data_dir, log_path):
        spawns.append(port)
        listener["open"] = listener["open"] | {str(port)}
        return FakeProcess()

    service = bundled_service(
        tmp_path,
        runtime,
        spawn=spawn,
        # The foreign endpoint never proves itself; only our own port does.
        transport=lambda host, port: FakeTransport(is_tor=port != 9050),
    )
    snapshot = run(service.ensure(reason="startup"))

    assert spawns == [9150], "a port somebody else holds must never be taken"
    assert snapshot["state"] == "ready"
    assert snapshot["port_conflict"] is True
    assert snapshot["endpoint"] == {"host": "127.0.0.1", "port": 9150}
    assert "9050" in listener["open"], "the foreign listener is left exactly as it was"


def test_our_own_endpoint_is_probed_before_the_compatibility_ports(tmp_path, listener, ports):
    """A foreign listener on 9050 must not make Canalla start a second daemon of its own."""
    runtime = stage_runtime(tmp_path)
    spawns = []

    def spawn(path, port, data_dir, log_path):
        spawns.append(port)
        listener["open"] = listener["open"] | {str(port)}
        return FakeProcess()

    service = bundled_service(tmp_path, runtime, spawn=spawn)
    assert run(service.ensure(reason="startup"))["state"] == "ready"
    assert spawns == [9050]

    # Somebody else starts listening on 9050 while our daemon keeps running on its own port.
    service._process_port = 9150
    listener["open"] = {"9150", "9050"}

    snapshot = run(service.ensure(reason="supervise"))

    assert snapshot["state"] == "ready"
    assert snapshot["endpoint"] == {"host": "127.0.0.1", "port": 9150}
    assert spawns == [9050], "our own endpoint is checked first: no second process"


def test_a_crash_loop_backs_off_instead_of_spinning(tmp_path, listener, ports):
    runtime = stage_runtime(tmp_path)

    def spawn(path, port, data_dir, log_path):
        return FakeProcess(exit_code=1)  # dies immediately, every time

    service = bundled_service(tmp_path, runtime, spawn=spawn, tor_startup_timeout_seconds=10.0)
    delays = []
    for _ in range(4):
        run(service.ensure(reason="supervise"))
        delays.append(service._supervise_delay())

    assert delays == [2.0, 5.0, 15.0, 30.0]


def test_the_managed_daemon_gets_our_own_configuration(tmp_path, monkeypatch):
    runtime = stage_runtime(tmp_path)
    settings = make_settings(tmp_path, alex_tor_runtime_dir=str(runtime))
    calls = []

    class FakePopen:
        def __init__(self, args, **kwargs):
            calls.append((args, kwargs))

    monkeypatch.setattr(tor_service.subprocess, "Popen", FakePopen)
    service = TorService(settings)
    data_dir = tmp_path / "tor"
    log_path = tmp_path / "logs" / "tor.log"

    service._spawn_tor(runtime / "tor.exe", 9151, data_dir, log_path)

    torrc = (data_dir / "torrc").read_text(encoding="utf-8")
    assert "ClientOnly 1" in torrc
    assert "SocksPort 127.0.0.1:9151" in torrc
    assert f"DataDirectory {data_dir}" in torrc
    assert f"Log notice file {log_path}" in torrc
    assert f"GeoIPFile {runtime / 'geoip'}" in torrc
    assert "SafeSocks 1" in torrc, "a locally resolved destination must be refused"
    assert (data_dir / "torrc-defaults").is_file()

    args, kwargs = calls[0]
    assert args == [
        str(runtime / "tor.exe"),
        "-f",
        str(data_dir / "torrc"),
        "--defaults-torrc",
        str(data_dir / "torrc-defaults"),
        "--ignore-missing-torrc",
    ]
    assert kwargs["cwd"] == str(runtime)


def test_the_managed_daemon_can_find_the_libraries_beside_it(tmp_path, monkeypatch):
    """The daemon Canalla ships carries its libraries next to itself and has no rpath, and a POSIX
    loader does not search the executable's own directory: without `LD_LIBRARY_PATH` the process
    dies at exec (exit 127) before Tor starts, which a supervisor can only report as a crash loop.
    Windows needs no equivalent - its loader searches there already."""
    runtime = stage_runtime(tmp_path)
    settings = make_settings(tmp_path, alex_tor_runtime_dir=str(runtime))
    calls = []

    class FakePopen:
        def __init__(self, args, **kwargs):
            calls.append((args, kwargs))

    monkeypatch.setattr(tor_service.subprocess, "Popen", FakePopen)
    TorService(settings)._spawn_tor(runtime / "tor", 9050, tmp_path / "tor", tmp_path / "logs" / "tor.log")

    _args, kwargs = calls[0]
    if sys.platform == "win32":
        assert kwargs["env"] is None, "Windows keeps the inherited environment as it always was"
    else:
        assert str(runtime) in kwargs["env"]["LD_LIBRARY_PATH"].split(os.pathsep)


def test_port_availability_reflects_a_real_listener(tmp_path):
    import socket

    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        port = int(held.getsockname()[1])
        assert tor_service.port_available("127.0.0.1", port) is False

    assert tor_service.port_available("127.0.0.1", port) is True


def test_a_quit_ends_the_process_it_owns_and_leaves_no_socks_port(tmp_path, listener, ports):
    runtime = stage_runtime(tmp_path)
    process = FakeProcess()

    def spawn(path, port, data_dir, log_path):
        listener["open"] = {str(port)}
        return process

    service = bundled_service(tmp_path, runtime, spawn=spawn)
    run(service.ensure(reason="startup"))

    run(service.stop())

    assert process.terminated == 1
    assert service.snapshot()["managed_port"] is None
    assert service.snapshot()["state"] == "unavailable"
