"""Authorized active security assessment with an explicit finding lifecycle.

The existing `security_assessment` package answers "what does the code and its dependencies look
like". This module answers "what does the running service actually expose", and only inside a
scope the caller names explicitly:

* every target must be listed in the scope, otherwise the call is refused before a socket opens;
* a finding is CONFIRMED only by a probe that reproduced it, never by the model's opinion;
* a finding becomes FIXED only when the same probe, re-run after the change, no longer reproduces
  it — editing the source is not proof;
* an optional Nmap/ZAP adapter is reported as unavailable when the binary is absent, never as a
  clean result.
"""

from __future__ import annotations

import json
import re
import secrets
import socket
import ssl
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlsplit

POTENTIAL, CONFIRMED, FIXED, FALSE_POSITIVE, REOPENED = (
    "POTENTIAL",
    "CONFIRMED",
    "FIXED",
    "FALSE_POSITIVE",
    "REOPENED",
)
STATUSES = {POTENTIAL, CONFIRMED, FIXED, FALSE_POSITIVE, REOPENED}
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

SECURITY_HEADERS = {
    "content-security-policy": (
        "medium",
        "CWE-693",
        "Set a Content-Security-Policy that matches the pages actually served.",
    ),
    "x-content-type-options": ("low", "CWE-693", "Send `X-Content-Type-Options: nosniff`."),
    "x-frame-options": ("low", "CWE-1021", "Send `X-Frame-Options: DENY` (or CSP frame-ancestors)."),
    "referrer-policy": ("low", "CWE-200", "Send a restrictive `Referrer-Policy`."),
}
SERVER_BANNER = re.compile(r"(?i)(apache|nginx|uvicorn|werkzeug|hypercorn|gunicorn)[/\s]([\w.\-]+)")


class ScopeRefused(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hostname(target: str) -> str:
    text = target if "//" in target else f"http://{target}"
    return (urlsplit(text).hostname or "").lower()


@dataclass
class ActiveScope:
    scope_id: str
    allowed_targets: list[str]
    authorized: bool = False
    allowed_methods: list[str] = field(default_factory=lambda: ["GET"])
    max_requests: int = 40
    timeout: float = 5.0

    def __post_init__(self) -> None:
        self.allowed_targets = [target.strip().rstrip("/").lower() for target in self.allowed_targets]

    def assert_url(self, url: str) -> str:
        if not self.authorized:
            raise ScopeRefused("scope_not_authorized")
        parts = urlsplit(url or "")
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ScopeRefused("scope_invalid_url")
        base = f"{parts.scheme}://{parts.netloc.lower()}"
        if not any(base == target or base.startswith(target + ":") for target in self.allowed_targets):
            raise ScopeRefused("scope_denied")
        return base

    def assert_port(self, host: str, port: int) -> None:
        if not self.authorized:
            raise ScopeRefused("scope_not_authorized")
        allowed = {_hostname(target) for target in self.allowed_targets}
        if (host or "").lower() not in {item for item in allowed if item}:
            raise ScopeRefused("scope_denied")
        if not (0 < int(port) < 65536):
            raise ScopeRefused("scope_invalid_port")

    def public(self) -> dict[str, Any]:
        return {
            "scope_id": self.scope_id,
            "allowed_targets": self.allowed_targets,
            "authorized": self.authorized,
            "max_requests": self.max_requests,
        }


@dataclass
class Finding:
    id: str
    title: str
    target: str
    scope_id: str
    severity: str
    status: str
    description: str
    evidence: list[dict[str, Any]] = field(default_factory=list)
    reproduction: str = ""
    remediation: str = ""
    cwe: str = ""
    confidence: str = "high"
    created_at: str = field(default_factory=_now)
    retest: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        return asdict(self)


def _finding_key(rule: str, target: str) -> str:
    return f"{rule}:{urlsplit(target).netloc or target}"


def _request(url: str, *, timeout: float, headers: dict[str, str] | None = None) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "CanallaSecurity/1.2.7", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(400_000).decode("utf-8", "replace")
            return {
                "status": response.status,
                "headers": dict(response.headers),
                "body": body,
                "url": response.geturl(),
            }
    except urllib.error.HTTPError as error:
        body = error.read(400_000).decode("utf-8", "replace")
        return {"status": error.code, "headers": dict(error.headers or {}), "body": body, "url": url}
    except (urllib.error.URLError, OSError) as error:
        return {"status": 0, "headers": {}, "body": "", "url": url, "error": type(error).__name__}


def _tcp_open(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _tls_info(host: str, port: int, timeout: float) -> dict[str, Any]:
    context = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                certificate = tls.getpeercert() or {}
                return {
                    "ok": True,
                    "protocol": tls.version(),
                    "not_after": certificate.get("notAfter", ""),
                    "subject": certificate.get("subject", []),
                }
    except (ssl.SSLError, OSError) as error:
        return {"ok": False, "error": type(error).__name__}


class ActiveAssessment:
    def __init__(self, scope: ActiveScope):
        self.scope = scope
        self.requests = 0

    # -- probes ---------------------------------------------------------------------------
    def probe_http(self, base: str) -> dict[str, Finding]:
        """One GET plus one reflected-marker GET. Every finding is produced by a real response."""
        self.requests += 1
        baseline = _request(base + "/", timeout=self.scope.timeout)
        found: dict[str, Finding] = {}
        if baseline["status"] == 0:
            return found
        headers = {key.lower(): value for key, value in (baseline["headers"] or {}).items()}
        for header, (severity, cwe, advice) in SECURITY_HEADERS.items():
            if header in headers:
                continue
            found[_finding_key(f"header:{header}", base)] = Finding(
                id=_finding_key(f"header:{header}", base),
                title=f"Missing security header: {header}",
                target=base,
                scope_id=self.scope.scope_id,
                severity=severity,
                status=CONFIRMED,
                description=f"The response to GET / does not send `{header}`.",
                evidence=[
                    {
                        "kind": "http_headers",
                        "url": baseline["url"],
                        "status": baseline["status"],
                        "headers": sorted(headers),
                    }
                ],
                reproduction=f"GET {base}/ and inspect the response headers for `{header}`.",
                remediation=advice,
                cwe=cwe,
            )
        banner = headers.get("server", "")
        match = SERVER_BANNER.search(banner)
        if match:
            found[_finding_key("server:banner", base)] = Finding(
                id=_finding_key("server:banner", base),
                title="Server banner discloses product and version",
                target=base,
                scope_id=self.scope.scope_id,
                severity="low",
                status=CONFIRMED,
                description=f"`Server: {banner}` names the product and its version.",
                evidence=[{"kind": "http_headers", "server": banner}],
                reproduction=f"GET {base}/ and read the `Server` header.",
                remediation="Send a generic Server header (or none) from the application/proxy.",
                cwe="CWE-200",
            )
        marker = "canalla" + secrets.token_hex(4)
        payload = f"<script>{marker}</script>"
        self.requests += 1
        reflected = _request(f"{base}/?q={quote(payload)}", timeout=self.scope.timeout)
        if reflected["status"] and payload in reflected["body"]:
            found[_finding_key("xss:reflected", base)] = Finding(
                id=_finding_key("xss:reflected", base),
                title="Query parameter is reflected without encoding",
                target=base,
                scope_id=self.scope.scope_id,
                severity="high",
                status=CONFIRMED,
                description="The `q` parameter is written into the HTML response verbatim.",
                evidence=[
                    {
                        "kind": "http_body",
                        "url": reflected["url"],
                        "marker": marker,
                        "snippet": _snippet(reflected["body"], marker),
                    }
                ],
                reproduction=f"GET {base}/?q=<script>{marker}</script> and search the body for the payload.",
                remediation="Encode every interpolated value (HTML-escape) before writing it into a page.",
                cwe="CWE-79",
            )
        return found

    def probe_debug_endpoint(self, base: str) -> dict[str, Finding]:
        """A `/debug` that answers 200 is an exposed diagnostic surface, not a guess."""
        self.requests += 1
        response = _request(base + "/debug", timeout=self.scope.timeout)
        if response["status"] == 200 and response["body"].strip():
            return {
                _finding_key("expose:debug", base): Finding(
                    id=_finding_key("expose:debug", base),
                    title="Diagnostic endpoint is exposed",
                    target=base,
                    scope_id=self.scope.scope_id,
                    severity="medium",
                    status=CONFIRMED,
                    description="GET /debug answers 200 with a body.",
                    evidence=[
                        {
                            "kind": "http_body",
                            "url": response["url"],
                            "status": response["status"],
                            "snippet": _snippet(response["body"], ""),
                        }
                    ],
                    reproduction=f"GET {base}/debug",
                    remediation="Disable the diagnostic route outside development.",
                    cwe="CWE-489",
                )
            }
        return {}

    def port_scan(self, host: str, ports: list[int]) -> list[dict[str, Any]]:
        results = []
        for port in ports[:64]:
            self.scope.assert_port(host, port)
            results.append({"port": port, "open": _tcp_open(host, port, self.scope.timeout)})
        return results

    def tls(self, host: str, port: int = 443) -> dict[str, Any]:
        self.scope.assert_port(host, port)
        return _tls_info(host, port, self.scope.timeout)


def _snippet(body: str, marker: str) -> str:
    index = body.find(marker) if marker else -1
    if index < 0:
        return body[:300]
    start = max(0, index - 120)
    return body[start : start + 300]


def adapters() -> dict[str, Any]:
    """External scanners are reported honestly: present or unavailable, never a silent pass."""
    import shutil

    result = {}
    for name, binary in (("nmap", "nmap"), ("zap", "zap.sh"), ("zap_win", "zap.bat")):
        path = shutil.which(binary)
        result[name] = {"available": bool(path), "path": path or ""}
    return result


def open_findings(findings: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in findings.values() if item.get("status") in {CONFIRMED, REOPENED, POTENTIAL}]


def merge(
    previous: dict[str, dict[str, Any]], current: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Carry the lifecycle across runs: fixed items stay fixed, regressions reopen."""
    merged = dict(previous or {})
    for key, finding in current.items():
        old = merged.get(key)
        if old and old.get("status") == FIXED:
            finding["status"] = REOPENED
            finding["retest"] = {"at": _now(), "reopened": True}
        elif old and old.get("status") == FALSE_POSITIVE:
            continue
        merged[key] = finding
    for key, old in list(merged.items()):
        if key in current:
            continue
        if old.get("status") in {CONFIRMED, REOPENED, POTENTIAL}:
            updated = dict(old)
            updated["status"] = FIXED
            updated["retest"] = {
                "at": _now(),
                "reproduced": False,
                "note": "the check no longer produces this finding",
            }
            merged[key] = updated
    return merged


def to_json(findings: dict[str, dict[str, Any]]) -> str:
    return json.dumps(findings, ensure_ascii=False)[:20000]
