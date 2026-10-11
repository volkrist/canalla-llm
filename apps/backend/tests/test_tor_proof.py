"""A08 — a proven route before a byte, bound to the endpoint and the process.

The defect: a Tor fetch could hand a URL to whatever listened on the SOCKS port, with no current
proof that the route was Tor. These cases pin the fixed contract — no URL leaves before a proof that
vouches for *this* endpoint (and, for a managed endpoint, *this* process), a stale proof is refreshed
with one bounded recovery, and a route that cannot be proven fails closed with ``tor_unavailable``
and no clearnet fallback.
"""

import asyncio
from datetime import timedelta

import pytest
from fakes_tor import FakeSocks5hServer, FakeTorService, dns_tripwire

from app.tools.contracts import ToolError
from app.tools.tor import service as tor_service
from app.tools.tor.provider import TorFetchArgs, TorFetchProvider
from app.tools.tor.service import TorService
from app.tools.tor.socks import TorTransport
from tests.settings_factory import make_settings

ONION = "duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad.onion"


class FakeProcess:
    """A managed process with an identity, so a proof can be bound to it."""

    def __init__(self, pid: int, *, exit_code: int | None = None):
        self.pid = pid
        self.exit_code = exit_code

    def poll(self):
        return self.exit_code

    def terminate(self):
        pass


# ------------------------------------------------------------------ the fetch-time proof gate


def test_a_plain_socks_listener_that_is_not_tor_never_receives_the_url(route_service):
    """A SOCKS proxy listens on the exact port, but nothing proved it is Tor: the URL stays home."""
    with FakeSocks5hServer() as server:
        service = route_service(FakeTorService(server.endpoint, verified=False))
        with pytest.raises(ToolError) as failure:
            asyncio.run(TorFetchProvider().execute(TorFetchArgs(urls=["http://example.com/"]), None))
        requests = list(server.requests)

    assert failure.value.code == "tor_unavailable"
    assert requests == [], "an unproven endpoint must never be handed the destination"
    assert service.recovery_calls == 1
    assert service.snapshot_calls == 0


def test_a_current_proof_lets_the_fetch_through(route_service):
    with FakeSocks5hServer() as server:
        service = route_service(FakeTorService(server.endpoint, verified=True))
        result = asyncio.run(TorFetchProvider().execute(TorFetchArgs(urls=["http://example.com/"]), None))
        requests = list(server.requests)

    assert requests == [("example.com", 80, 3)]
    assert result.sources[0]["reachable"] is True
    assert result.metadata["verified_chain"] is True
    assert service.recovery_calls == 0  # a proven route is not recovered


def test_no_local_dns_and_no_clearnet_when_the_route_is_unproven(monkeypatch, route_service):
    """The unproven path cannot fall back: no name is resolved here and no proxy sees the URL."""
    attempts, install_loop_guard = dns_tripwire(monkeypatch)

    with FakeSocks5hServer() as server:
        service = route_service(FakeTorService(server.endpoint, verified=False))

        async def run():
            await install_loop_guard()
            with pytest.raises(ToolError) as failure:
                await TorFetchProvider().execute(TorFetchArgs(urls=["http://example.com/"]), None)
            return failure.value.code

        assert asyncio.run(run()) == "tor_unavailable"
        assert list(server.requests) == []

    assert attempts == []
    assert service.recovery_calls == 1


def test_a_route_without_a_service_fails_closed(monkeypatch):
    """No service to prove the route: the action refuses rather than guessing an endpoint."""
    tor_service.set_active_service(None)
    with pytest.raises(ToolError) as failure:
        asyncio.run(TorFetchProvider().execute(TorFetchArgs(urls=["http://example.com/"]), None))
    assert failure.value.code == "tor_unavailable"


def test_the_route_recovers_once_on_a_transport_error_and_stays_in_tor(route_service):
    with FakeSocks5hServer(refuse=["refused.example"]) as server:
        service = route_service(FakeTorService(server.endpoint, verified=True, recover=True))
        with pytest.raises(ToolError) as failure:
            asyncio.run(TorFetchProvider().execute(TorFetchArgs(urls=["http://refused.example/"]), None))
        requests = list(server.requests)

    assert failure.value.code == "tor_unavailable"
    # One recovery inside the budget, then one retry — both through the same proven SOCKS route.
    assert service.recovery_calls == 1
    assert requests == [("refused.example", 80, 3), ("refused.example", 80, 3)]


def test_a_missing_proof_is_refreshed_before_the_fetch(route_service):
    """An unproven (or expired) route is re-established first; only then does the URL travel."""
    with FakeSocks5hServer() as server:
        service = route_service(FakeTorService(server.endpoint, verified=False, recover=True))
        result = asyncio.run(TorFetchProvider().execute(TorFetchArgs(urls=["http://example.com/"]), None))
        requests = list(server.requests)

    assert service.recovery_calls == 1  # the proof was renewed before the request
    assert requests == [("example.com", 80, 3)]
    assert result.sources[0]["reachable"] is True
    assert result.metadata["verified_chain"] is True


def test_the_gate_wants_the_proof_for_the_endpoint_it_will_use(route_service):
    """A proof for a *different* endpoint is not a proof for this one."""

    class MovedEndpoint(FakeTorService):
        def active(self):
            return (self.endpoint[0], self.endpoint[1] + 1)  # the route now points somewhere else

    with FakeSocks5hServer() as server:
        service = route_service(MovedEndpoint(server.endpoint, verified=True))
        with pytest.raises(ToolError) as failure:
            asyncio.run(TorFetchProvider().execute(TorFetchArgs(urls=["http://example.com/"]), None))
        requests = list(server.requests)

    assert failure.value.code == "tor_unavailable"
    assert requests == []
    assert service.recovery_calls == 1


# ------------------------------------------------------------------- redirects stay on Tor


def test_a_redirect_to_a_local_literal_is_refused_and_never_followed(route_service):
    def responder(host, port, request):
        if request.startswith(b"GET /start"):
            return b"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1/secret\r\nContent-Length: 0\r\n\r\n"
        return b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\nland"

    with FakeSocks5hServer(responder=responder) as server:
        with pytest.raises(ToolError) as failure:
            asyncio.run(TorTransport(host="127.0.0.1", port=server.port).fetch("http://example.com/start"))
        requests = list(server.requests)

    assert failure.value.code == "unsafe_url"
    # Only the first hop happened; the local redirect was never requested.
    assert requests == [("example.com", 80, 3)]


def test_a_redirect_to_another_onion_stays_on_the_same_verified_route(route_service):
    def responder(host, port, request):
        if request.startswith(b"GET /start"):
            return (
                f"HTTP/1.1 302 Found\r\nLocation: http://{ONION}/final\r\nContent-Length: 0\r\n\r\n"
            ).encode()
        return b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\nland"

    with FakeSocks5hServer(responder=responder) as server:
        page = asyncio.run(TorTransport(host="127.0.0.1", port=server.port).fetch("http://example.com/start"))
        requests = list(server.requests)

    assert page["text"] == "land"
    assert page["url"].endswith("/final")
    # Both hops travel as names (ATYP 3) through the same SOCKS endpoint — Tor is not bypassed.
    assert [host for host, _port, _atyp in requests] == ["example.com", ONION]
    assert all(atyp == 3 for _host, _port, atyp in requests)


# ------------------------------------------------------- proof binding at the service level


def _bound_service(tmp_path, *, pid=111, ttl=900, clock=None) -> TorService:
    settings = make_settings(
        alex_llm_data_dir=str(tmp_path),
        tor_proof_ttl_seconds=ttl,
    )
    service = TorService(
        settings,
        clock=clock,
        transport_factory=lambda host, port: None,
        proof_path=tmp_path / "tor.json",
    )
    service._endpoint = ("127.0.0.1", 9050)
    service._listening = True
    service._process = FakeProcess(pid)
    service._managed_source = True
    service._save_proof("127.0.0.1", 9050, "managed", pid=pid)
    return service


def test_a_managed_proof_is_bound_to_its_process(tmp_path):
    service = _bound_service(tmp_path, pid=111)
    assert service.proven_endpoint() == ("127.0.0.1", 9050)

    # The managed process died and a new one took over the endpoint: the old proof may not vouch.
    service._process = FakeProcess(222)
    assert service.proof_process_matches() is False
    assert service.proven_endpoint() is None

    # The process is gone entirely: still not proven.
    service._process = None
    assert service.proven_endpoint() is None


def test_proven_endpoint_requires_the_same_endpoint(tmp_path):
    service = _bound_service(tmp_path, pid=111)
    assert service.proven_endpoint() == ("127.0.0.1", 9050)

    # The port moved: the stored proof belonged to the old endpoint.
    service._endpoint = ("127.0.0.1", 9150)
    assert service.proof_endpoint_matches() is False
    assert service.proven_endpoint() is None


def test_an_expired_proof_reports_no_proven_endpoint(tmp_path):
    now = {"value": tor_service.datetime(2026, 10, 11, 9, 0, tzinfo=tor_service.timezone.utc)}
    service = _bound_service(tmp_path, pid=111, ttl=60, clock=lambda: now["value"])
    assert service.proven_endpoint() == ("127.0.0.1", 9050)

    now["value"] = now["value"] + timedelta(seconds=61)
    assert service.proof_fresh() is False
    assert service.proven_endpoint() is None


def test_an_external_proof_has_no_process_to_bind_to(tmp_path):
    settings = make_settings(alex_llm_data_dir=str(tmp_path))
    service = TorService(
        settings,
        transport_factory=lambda host, port: None,
        proof_path=tmp_path / "tor.json",
    )
    service._endpoint = ("127.0.0.1", 9150)
    service._listening = True
    service._save_proof("127.0.0.1", 9150, "external")

    # Somebody else's Tor: endpoint and TTL bind it, and we do not pretend to own a process.
    assert service.proof_process_matches() is True
    assert service.proven_endpoint() == ("127.0.0.1", 9150)
