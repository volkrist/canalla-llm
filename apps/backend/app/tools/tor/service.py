"""The Tor service: ready without being asked, and honest when it is not.

Tor used to be an assumption. The product expected something else to be listening on
``127.0.0.1:9050`` and reported ``unavailable`` when nothing was, so a normal install showed a red
Tor chip until the user started Tor Browser by hand — and the status path deliberately never
reported ``ready`` at all. This module owns the service instead:

* **discovery** — an endpoint that already works (the configured port, then 9050, then 9150) is used
  as it is, whoever started it;
* **management** — when nothing answers and a Tor binary exists (the installed Tor Browser bundle,
  a standalone ``tor``, or an explicit override), exactly one managed process is started with its
  own data directory and a loopback SOCKS port, and it is stopped when the backend stops;
* **proof** — ``ready`` is only reported after a real SOCKS5h round trip to a Tor-gated endpoint
  confirms the exit really is Tor. The proof is persisted, so the status path stays read-only and
  never performs a network call;
* **recovery** — bounded supervision re-probes, restarts a managed process that died and
  re-verifies, without a busy loop and without a second controller of anything.

Rules this module must never break:

* **no clearnet fallback.** A request that needs Tor fails closed; nothing here widens a route.
* **loopback only.** Tor is reached on 127.0.0.1/::1 and never exposed on a public interface.
* **no secrets and no identities.** The proof stores the fact of a verified route plus the endpoint
  it was verified through — never an exit address, an IP or a fingerprint.
* **one process.** A managed Tor is started only when no endpoint answers, and only one is owned.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...config import Settings
from ...data_paths import application_data
from .browser import existing_tor_browser, socks_listening
from .socks import TorTransport

logger = logging.getLogger(__name__)

READY = "ready"
STARTING = "starting"
CONFIGURED = "configured"
RECONNECTING = "reconnecting"
UNAVAILABLE = "unavailable"

# Typed reasons. The UI maps them to messages; nothing here is a user-facing string.
NOT_INSTALLED = "tor_not_installed"
NO_ENDPOINT = "tor_no_endpoint"
CIRCUIT_INVALID = "tor_circuit_invalid"
CHECK_FAILED = "tor_check_failed"
START_FAILED = "tor_start_failed"
DISABLED = "tor_managed_disabled"
OFFLINE = "tor_offline"

# A managed start is given room to bootstrap (a cold Tor needs ~20-40 s) but never an unbounded one.
BOOTSTRAP_POLL_SECONDS = 1.5
STARTING_DELAY_SECONDS = 2.0
STOP_TIMEOUT_SECONDS = 8.0
BINARY_SEARCH_NAMES = ("tor.exe", "tor")
# The runtime Canalla ships, relative to the installed application, and what the fetch step wrote
# next to it (version, license, provenance).
BUNDLED_SOURCE = "bundled"
BUNDLED_RUNTIME_PARTS = ("runtime", "tor")
RUNTIME_METADATA_NAME = "runtime.json"
# Our own Tor never takes a port somebody else holds, and a restarting process backs off.
PORT_PROBE_SECONDS = 0.3
# How long an endpoint that is not ours gets to prove itself before our own runtime takes over.
DISCOVERY_PROOF_SECONDS = 5.0
RESTART_BACKOFF_SECONDS = (2.0, 5.0, 15.0, 30.0)


def tor_binary_from_browser() -> Path | None:
    """The ``tor`` daemon shipped inside the installed Tor Browser, if there is one.

    Tor Browser keeps it next to its own ``firefox.exe``: ``Browser/TorBrowser/Tor/tor.exe``.
    Using the user's own installed binary is the supported path — nothing is redistributed.
    """
    browser = existing_tor_browser()
    if browser is None:
        return None
    tor_dir = browser.parent / "TorBrowser" / "Tor"
    for name in BINARY_SEARCH_NAMES:
        candidate = tor_dir / name
        if candidate.is_file():
            return candidate
    return None


def bundled_runtime_dir(settings: Settings) -> Path | None:
    """The Tor runtime Canalla ships, when this installation has one.

    The Desktop hands the path over (``ALEX_TOR_RUNTIME_DIR``); a packaged backend can also find it
    next to its own installation, which is where the installer puts it (``runtime/tor``).
    """
    configured = (settings.alex_tor_runtime_dir or "").strip()
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_dir() else None
    if os.environ.get("ALEX_PACKAGED") == "1":
        install = Path(sys.executable).resolve().parent.parent.parent
        candidate = install.joinpath(*BUNDLED_RUNTIME_PARTS)
        if candidate.is_dir():
            return candidate
    return None


def bundled_runtime_metadata(settings: Settings) -> dict[str, Any] | None:
    """What the fetch step recorded about the bundled runtime: version, license, provenance."""
    runtime = bundled_runtime_dir(settings)
    if runtime is None:
        return None
    try:
        data = json.loads((runtime / RUNTIME_METADATA_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def bundled_tor_binary(settings: Settings) -> tuple[Path, str] | None:
    """The daemon Canalla ships: the path that requires nothing installed by the user."""
    runtime = bundled_runtime_dir(settings)
    if runtime is None:
        return None
    for name in BINARY_SEARCH_NAMES:
        candidate = runtime / name
        if candidate.is_file():
            return candidate, BUNDLED_SOURCE
    return None


def port_available(host: str, port: int) -> bool:
    """True when nothing answers on the loopback port and we can bind it ourselves."""
    if socks_listening(host, port, PORT_PROBE_SECONDS):
        return False
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def find_tor_binary(settings: Settings) -> tuple[Path, str] | None:
    """``(path, source)`` for the Tor daemon to manage, or ``None`` when there is none.

    The runtime Canalla ships comes first: an installation must not depend on whatever the machine
    happens to have. An explicit override stays available for an operator, and the compatibility
    paths (the user's Tor Browser, a standalone Tor) stay for machines without a bundle.
    """
    override = (settings.tor_binary_path or "").strip()
    if override:
        path = Path(override).expanduser()
        if path.is_file():
            return path, "configured"
    bundled = bundled_tor_binary(settings)
    if bundled is not None:
        return bundled
    from_browser = tor_binary_from_browser()
    if from_browser is not None:
        return from_browser, "tor_browser"
    found = shutil.which("tor")
    if found:
        return Path(found), "standalone"
    for base in (
        Path(os.environ.get("ProgramFiles") or r"C:\Program Files") / "Tor",
        Path(os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)") / "Tor",
    ):
        for name in BINARY_SEARCH_NAMES:
            candidate = base / name
            if candidate.is_file():
                return candidate, "standalone"
    return None


# The running service, so stateless tool providers inherit the endpoint that was actually proven
# (a Tor Browser on 9150, a standalone Tor on 9050, or the managed process). Nothing else reads
# this: the transport still opens its own SOCKS5h connections and never falls back to clearnet.
_ACTIVE_SERVICE: "TorService | None" = None


def set_active_service(service: "TorService | None") -> None:
    global _ACTIVE_SERVICE
    _ACTIVE_SERVICE = service


def active_endpoint(fallback_host: str, fallback_port: int) -> tuple[str, int]:
    """The (host, port) tools must route through; the configured one before discovery."""
    if _ACTIVE_SERVICE is None:
        return fallback_host, fallback_port
    return _ACTIVE_SERVICE.active()


class TorService:
    """Discovery, management, proof and recovery for the Tor route."""

    def __init__(
        self,
        settings: Settings,
        *,
        clock=None,
        spawn=None,
        transport_factory=None,
        proof_path: Path | None = None,
    ):
        self.settings = settings
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        # Test seams: a launcher and the transport used for the proof.
        self.spawn = spawn or self._spawn_tor
        self.transport_factory = transport_factory or (lambda host, port: TorTransport(host=host, port=port))
        self._proof_path = proof_path or (application_data(settings) / "runtime" / "tor.json")
        self._state = STARTING
        self._reason: str | None = None
        self._endpoint: tuple[str, int] | None = None
        self._listening = False
        self._managed_source = False
        self._port_conflict = False
        self._restarts = 0
        self._process: Any | None = None
        self._process_port: int | None = None
        self._proof: dict | None = self._load_proof()
        self._lock = asyncio.Lock()
        self._updated_at = self.clock()

    # ------------------------------------------------------------------------------ endpoints

    @property
    def host(self) -> str:
        return self.settings.tor_socks_host

    def candidate_ports(self) -> list[int]:
        """The ports worth trying, in order: the configured one, then the two Tor defaults."""
        ports: list[int] = []
        for port in [self.settings.tor_socks_port, 9050, 9150, *self.settings.tor_extra_ports]:
            if isinstance(port, int) and 0 < port < 65536 and port not in ports:
                ports.append(port)
        return ports

    def active(self) -> tuple[str, int]:
        """The endpoint tools must use. Falls back to the configured one before discovery."""
        return self._endpoint or (self.host, self.settings.tor_socks_port)

    # --------------------------------------------------------------------------------- proof

    def _load_proof(self) -> dict | None:
        try:
            data = json.loads(self._proof_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or data.get("verified") is not True:
            return None
        return data

    def _save_proof(self, host: str, port: int, source: str, *, pid: int | None = None) -> None:
        proof = {
            "verified": True,
            "verified_at": self.clock().isoformat(),
            "host": host,
            "port": port,
            "source": source,
            "method": "socks5h",
            # Which endpoint this is, and (only when we own it) which process serves it. Never an
            # exit address, never a fingerprint.
            "pid": pid,
            "tor_version": self.runtime_version(),
        }
        self._proof = proof
        try:
            self._proof_path.parent.mkdir(parents=True, exist_ok=True)
            self._proof_path.write_text(json.dumps(proof), encoding="utf-8")
        except OSError:
            logger.warning("tor_proof_write_failed")

    def _drop_proof(self) -> None:
        self._proof = None
        try:
            self._proof_path.unlink()
        except OSError:
            pass

    def proof_age_seconds(self) -> float | None:
        if not self._proof:
            return None
        try:
            stamp = datetime.fromisoformat(str(self._proof.get("verified_at")))
        except (TypeError, ValueError):
            return None
        return max(0.0, (self.clock() - stamp).total_seconds())

    def proof_fresh(self) -> bool:
        age = self.proof_age_seconds()
        return age is not None and age <= self.settings.tor_proof_ttl_seconds

    def proof_endpoint_matches(self) -> bool:
        """A proof only vouches for the endpoint it was taken through."""
        if not self._proof or not self._endpoint:
            return False
        host, port = self._endpoint
        return self._proof.get("host") == host and int(self._proof.get("port") or 0) == port

    def verified(self) -> bool:
        return bool(self.proof_fresh() and self.proof_endpoint_matches() and self._listening)

    # ------------------------------------------------------------------------------- status

    def snapshot(self) -> dict[str, Any]:
        """Read-only view for the status surface. Never touches the network or the process.

        A service that was ready but whose proof has aged out reports ``configured``: the endpoint
        still answers, the route is simply not proven right now — and the supervisor re-proves it
        on its next tick. It must never keep claiming ``ready`` on an expired proof.
        """
        if self.verified():
            state = READY
        elif self._state == READY and self._listening:
            state = CONFIGURED
        else:
            state = self._state
        binary = find_tor_binary(self.settings)
        return {
            "state": state,
            "reason": None if self.verified() else self._reason,
            "managed": self._managed_source and self.verified(),
            "endpoint": ({"host": self._endpoint[0], "port": self._endpoint[1]} if self._endpoint else None),
            "listening": self._listening,
            "verified_chain": self.verified(),
            "verified_at": (self._proof or {}).get("verified_at"),
            "proof_age_seconds": self.proof_age_seconds(),
            "proof_ttl_seconds": self.settings.tor_proof_ttl_seconds,
            "method": "socks5h",
            "candidates": self.candidate_ports(),
            "binary": ({"path": str(binary[0]), "source": binary[1]} if binary else None),
            "runtime_version": self.runtime_version(),
            "managed_port": self._process_port,
            "port_conflict": self._port_conflict,
            "fallback": "none",
            "updated_at": self._updated_at.isoformat(),
        }

    def _set(self, state: str, reason: str | None) -> None:
        self._state = state
        self._reason = reason
        self._updated_at = self.clock()

    # -------------------------------------------------------------------------- verification

    async def _prove(self, host: str, port: int) -> bool:
        """One real SOCKS5h round trip. True only when the exit reports itself as Tor."""
        transport = self.transport_factory(host, port)
        try:
            page = await asyncio.wait_for(
                transport.fetch(self.settings.tor_check_url, timeout=20),
                timeout=25,
            )
        except Exception as error:
            logger.debug("tor_proof_failed type=%s", type(error).__name__)
            return False
        if int(page.get("status") or 0) != 200:
            return False
        try:
            body = json.loads(page.get("text") or "{}")
        except ValueError:
            return False
        # The exit's address is deliberately never stored or logged; only the fact matters.
        return bool(isinstance(body, dict) and body.get("IsTor") is True)

    async def _await_proof(self, port: int, *, deadline_seconds: float) -> bool:
        """Wait for a *bootstrapped* Tor.

        Tor opens its SOCKS listener long before it has a consensus and can build a circuit, so a
        sockopen port is not readiness. The proof is retried inside a bounded window instead; a
        managed process that dies during that window fails fast.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + deadline_seconds
        while True:
            if self._process is not None and getattr(self._process, "poll", lambda: None)() is not None:
                logger.warning("tor_managed_exited code=%s", getattr(self._process, "returncode", None))
                return False
            if await self._prove(self.host, port):
                return True
            if loop.time() >= deadline:
                return False
            await asyncio.sleep(BOOTSTRAP_POLL_SECONDS)

    # ------------------------------------------------------------------- managed process

    def _torrc_path(self, data_dir: Path) -> Path:
        return data_dir / "torrc"

    def _log_path(self) -> Path:
        return application_data(self.settings) / "logs" / "tor.log"

    def runtime_version(self) -> str | None:
        """The version of the runtime this installation ships, as the fetch step recorded it."""
        metadata = bundled_runtime_metadata(self.settings) or {}
        for key in ("daemon_version", "version"):
            value = metadata.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    def _process_pid(self) -> int | None:
        pid = getattr(self._process, "pid", None)
        return int(pid) if isinstance(pid, int) else None

    def _discovery_proof_seconds(self) -> float:
        """How long to wait for an endpoint that is not ours to prove itself.

        A working endpoint proves within seconds, so this stays short when Canalla has a runtime of
        its own: waiting a full window on somebody else's endpoint would delay the path that needs
        nothing from the machine.
        """
        if find_tor_binary(self.settings) is not None:
            return min(DISCOVERY_PROOF_SECONDS, self.settings.tor_startup_timeout_seconds)
        return min(30.0, self.settings.tor_startup_timeout_seconds)

    def bootstrap_progress(self, *, since_seconds: float = 900.0) -> int | None:
        """The last bootstrap percentage Tor itself reported, or ``None`` when unknown.

        A cold Tor needs a consensus before it can build any circuit, and on a slow network that
        takes minutes. Reading Tor's own signal keeps the difference honest: still bootstrapping is
        *starting*, while a finished bootstrap with a failing proof is a real circuit problem.
        """
        try:
            lines = self._log_path().read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return None
        progress: int | None = None
        for line in reversed(lines[-400:]):
            marker = "Bootstrapped "
            if marker not in line:
                continue
            tail = line.split(marker, 1)[1]
            digits = ""
            for char in tail:
                if char.isdigit():
                    digits += char
                else:
                    break
            if digits:
                progress = int(digits)
                break
        return progress

    def _spawn_tor(self, binary: Path, port: int, data_dir: Path, log_path: Path):
        torrc = self._write_configs(binary, port, data_dir, log_path)
        args = [
            str(binary),
            "-f",
            str(torrc),
            "--defaults-torrc",
            str(data_dir / "torrc-defaults"),
            "--ignore-missing-torrc",
        ]
        creation = 0
        if sys.platform == "win32":  # pragma: no cover - Windows only
            creation = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        popen = getattr(subprocess, "Popen")
        return popen(
            args,
            cwd=str(binary.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation,
        )

    def _write_configs(self, binary: Path, port: int, data_dir: Path, log_path: Path) -> Path:
        """Write the configuration Canalla runs its own Tor with.

        The managed daemon reads these two files and nothing else: the user's global torrc is never
        read, never written and never needed. Paths are written natively, because Tor treats a
        forward-slashed Windows path as relative.
        """
        data_dir.mkdir(parents=True, exist_ok=True)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        torrc = self._torrc_path(data_dir)
        lines = [
            "# Written by Canalla LLM for the Tor it manages. Nothing here is user input.",
            "ClientOnly 1",
            f"SocksPort {self.host}:{port}",
            f"DataDirectory {data_dir}",
            f"Log notice file {log_path}",
            # Application connections must hand the hostname over: Tor refuses a local resolution.
            "SafeSocks 1",
        ]
        for flag, name in (("GeoIPFile", "geoip"), ("GeoIPv6File", "geoip6")):
            candidate = binary.parent / name
            if candidate.is_file():
                lines.append(f"{flag} {candidate}")
        torrc.write_text("\n".join(lines) + "\n", encoding="utf-8")
        (data_dir / "torrc-defaults").write_text(
            "# Exists so that no machine-wide defaults file can influence the Tor Canalla manages.\n",
            encoding="utf-8",
        )
        return torrc

    async def _start_managed(self, binary: tuple[Path, str], port: int) -> bool:
        path, source = binary
        data_dir = application_data(self.settings) / "tor"
        log_path = self._log_path()
        logger.info("tor_start_managed source=%s port=%s", source, port)
        self._set(STARTING, None)
        try:
            self._process = await asyncio.to_thread(self.spawn, path, port, data_dir, log_path)
        except Exception as error:
            logger.warning("tor_start_failed type=%s", type(error).__name__)
            self._process = None
            return False
        self._process_port = port
        self._restarts += 1
        return True

    def _managed_port(self) -> int:
        """The port our own Tor may use: the first candidate that nothing else holds.

        A port somebody else is listening on is never taken: Canalla does not stop, reconfigure or
        borrow a process it did not start. When every candidate is held, a free loopback port is
        chosen instead - the endpoint that is actually used is the one that gets proven and saved.
        """
        candidates = self.candidate_ports()
        for port in candidates:
            if port_available(self.host, port):
                self._port_conflict = port != candidates[0]
                return port
        self._port_conflict = True
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((self.host, 0))
            return int(probe.getsockname()[1])

    async def _wait_bootstrap(self, port: int) -> bool:
        """Is the managed SOCKS endpoint accepting connections at all (not yet a proof)?"""
        deadline = asyncio.get_running_loop().time() + self.settings.tor_startup_timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            if self._process is not None and getattr(self._process, "poll", lambda: None)() is not None:
                # The managed process exited during bootstrap.
                logger.warning("tor_managed_exited code=%s", getattr(self._process, "returncode", None))
                return False
            if await asyncio.to_thread(socks_listening, self.host, port, 0.4):
                return True
            await asyncio.sleep(BOOTSTRAP_POLL_SECONDS)
        return False

    def _stop_managed(self) -> None:
        """End the process we own. An external Tor is never touched here.

        A clean Quit must not leave a listening SOCKS port or a stray daemon behind: the process gets
        a bounded moment to exit and is then ended, so the next launch starts from a free port
        instead of an orphan.
        """
        process = self._process
        self._process = None
        self._process_port = None
        self._managed_source = False
        if process is None:
            return
        try:
            process.terminate()
        except Exception:
            pass
        wait = getattr(process, "wait", None)
        if wait is not None:
            try:
                wait(timeout=STOP_TIMEOUT_SECONDS)
                return
            except Exception:
                pass
        kill = getattr(process, "kill", None)
        if kill is not None:
            try:
                kill()
            except Exception:
                pass

    # -------------------------------------------------------------------------------- ensure

    def _probe_order(self) -> list[int]:
        """The ports to look at, ours first.

        An endpoint this service already owns is checked before the compatibility ports, so a
        foreign listener can never make Canalla start a second process next to its own.
        """
        ports = self.candidate_ports()
        own = self._process_port
        if own is None:
            return ports
        return [own, *[port for port in ports if port != own]]

    def _supervise_delay(self) -> float:
        """How long to wait before the next upkeep pass: a crash loop backs off, it does not spin."""
        if self._state in {STARTING, RECONNECTING}:
            return STARTING_DELAY_SECONDS
        if self._restarts:
            index = min(self._restarts - 1, len(RESTART_BACKOFF_SECONDS) - 1)
            return RESTART_BACKOFF_SECONDS[index]
        return self.settings.tor_supervise_seconds

    async def ensure(self, *, reason: str = "supervise") -> dict[str, Any]:
        """Make the service ready, or say precisely why it is not.

        The order is deliberate: never start a second Tor while a working one answers, and never
        report readiness without a proof taken through the endpoint that is actually in use.
        """
        async with self._lock:
            candidates = self._probe_order()

            # 1. An endpoint that already works (ours or somebody else's) is used as it is.
            unproven: int | None = None
            for port in candidates:
                listening = await asyncio.to_thread(socks_listening, self.host, port, 0.4)
                if not listening:
                    continue
                if self.verified() and self._endpoint == (self.host, port):
                    self._set(READY, None)
                    return self.snapshot()
                if await self._await_proof(port, deadline_seconds=self._discovery_proof_seconds()):
                    self._endpoint = (self.host, port)
                    self._listening = True
                    self._managed_source = self._process is not None and self._process_port == port
                    self._save_proof(self.host, port, "managed" if self._managed_source else "external")
                    self._set(READY, None)
                    return self.snapshot()
                # SOCKS answers but no proven route: fail closed and keep looking. An external Tor
                # that is still bootstrapping is not called broken either.
                self._listening = True
                unproven = port
                self._drop_proof()
                progress = self.bootstrap_progress()
                if progress is not None and progress < 100:
                    self._set(RECONNECTING, None)
                else:
                    self._set(UNAVAILABLE, CIRCUIT_INVALID)

            # 2. Nothing is proven. The runtime Canalla ships is the primary path: it is started on
            #    a port nobody else holds. An endpoint that answers without proving a route (a
            #    foreign Tor still bootstrapping, or a stray listener) is never taken over, never
            #    killed and never claimed as ours - it is simply not readiness.
            binary = find_tor_binary(self.settings)
            if not self.settings.tor_managed_enabled or binary is None:
                if unproven is not None:
                    # Nothing to manage: the loop already told the story (bootstrapping or broken).
                    return self.snapshot()
                self._listening = False
                self._set(
                    UNAVAILABLE,
                    DISABLED if not self.settings.tor_managed_enabled else NOT_INSTALLED,
                )
                return self.snapshot()

            if self._process is not None and getattr(self._process, "poll", lambda: None)() is not None:
                # The process we own died: recovery is a restart, not a red chip.
                self._stop_managed()
            if self._process is None or self._process_port is None:
                port = self._managed_port()
                if not await self._start_managed(binary, port):
                    self._set(UNAVAILABLE, START_FAILED)
                    return self.snapshot()
            port = int(self._process_port or 0)
            starting_state = STARTING if reason == "startup" else RECONNECTING
            self._set(starting_state, None)
            if not await self._wait_bootstrap(port):
                self._listening = False
                self._set(UNAVAILABLE, NO_ENDPOINT)
                self._drop_proof()
                return self.snapshot()
            self._listening = True
            if not await self._await_proof(port, deadline_seconds=self.settings.tor_startup_timeout_seconds):
                progress = self.bootstrap_progress()
                if progress is not None and progress < 100:
                    # Still bootstrapping: this is a transient state, not a broken route. The
                    # supervisor keeps waiting and the chip says «Tor подключается…».
                    self._set(starting_state, None)
                    return self.snapshot()
                self._set(UNAVAILABLE, CIRCUIT_INVALID)
                self._drop_proof()
                return self.snapshot()
            self._endpoint = (self.host, port)
            self._managed_source = True
            self._restarts = 0
            self._save_proof(self.host, port, "managed", pid=self._process_pid())
            self._set(READY, None)
            return self.snapshot()

    # ------------------------------------------------------------------------------ lifecycle

    async def supervise(self) -> None:
        """Bounded periodic upkeep: probe, restart a dead managed process, re-verify.

        The first pass runs immediately (that is what makes Tor ready after a normal launch); every
        later pass waits the configured interval, so this is upkeep and not polling.
        """
        first = True
        while True:
            if not first:
                await asyncio.sleep(self._supervise_delay())
            first = False
            try:
                if self._process is not None and getattr(self._process, "poll", lambda: None)() is not None:
                    # A managed Tor died: recovery is a restart plus a fresh proof, not a red chip.
                    self._stop_managed()
                    self._drop_proof()
                await self.ensure(
                    reason="startup" if self._state in {STARTING, RECONNECTING} else "supervise"
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:  # pragma: no cover - supervision must never die
                logger.warning("tor_supervise_failed type=%s", type(error).__name__)

    async def stop(self) -> None:
        """Stop the process we own. An external Tor is somebody else's and is left alone."""
        async with self._lock:
            self._stop_managed()
            self._listening = False
            self._endpoint = None
            self._set(UNAVAILABLE, NO_ENDPOINT)
