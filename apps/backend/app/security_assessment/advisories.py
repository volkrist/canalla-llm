"""Dependency advisories from OSV (https://osv.dev), with an injectable transport.

OSV aggregates the primary advisory databases Canalla is asked to use — GitHub Security Advisories,
PyPA, npm, and NVD/CVE aliases — and answers a version-specific question, which is the only one
that matters here: *is this exact version affected?* A public CVE that does not match the
installed version is not a finding, and a scanner that only searches by name produces exactly that
false positive.

The client is deliberately small and bounded: one batch query, a handful of detail lookups, a short
timeout. A network failure is a typed `advisory_lookup_unavailable`, never an empty result that
would read as "no vulnerabilities".
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

OSV_BATCH = "https://api.osv.dev/v1/querybatch"
OSV_VULN = "https://api.osv.dev/v1/vulns/"
ECOSYSTEMS = {"pypi": "PyPI", "npm": "npm"}


class AdvisoryError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(code)


@dataclass
class Package:
    ecosystem: str
    name: str
    version: str

    def as_query(self) -> dict:
        return {
            "package": {"name": self.name, "ecosystem": ECOSYSTEMS[self.ecosystem]},
            "version": self.version,
        }


def _http_json(method: str, url: str, body: dict | None, timeout: float) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "User-Agent": "canalla-llm-security"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed hosts
        return json.loads(response.read().decode("utf-8"))


def _severity(vuln: dict) -> str:
    labels = {str(item.get("type", "")).upper() for item in vuln.get("severity") or []}
    if "CRITICAL" in labels:
        return "critical"
    if "HIGH" in labels:
        return "high"
    if "MODERATE" in labels or "MEDIUM" in labels:
        return "medium"
    database = str(vuln.get("database_specific", {}).get("severity", "")).upper()
    if database in {"CRITICAL", "HIGH", "MODERATE", "MEDIUM", "LOW"}:
        return database.lower().replace("moderate", "medium")
    return "medium"


def _fixed_versions(vuln: dict, package: Package) -> list[str]:
    fixed: list[str] = []
    for affected in vuln.get("affected") or []:
        name = str((affected.get("package") or {}).get("name") or "").lower()
        if name and name != package.name.lower():
            continue
        for entry in affected.get("ranges") or []:
            for event in entry.get("events") or []:
                value = event.get("fixed")
                if value and value not in fixed:
                    fixed.append(str(value))
    return sorted(fixed)


def evaluate(vulns: list[dict], package: Package) -> list[dict]:
    """Turn OSV records into findings, keeping only what applies to *this* version."""
    findings = []
    for vuln in vulns:
        fixed = _fixed_versions(vuln, package)
        findings.append(
            {
                "kind": "dependency",
                "rule": "vulnerable_dependency",
                "severity": _severity(vuln),
                "confidence": "high",
                "package": package.name,
                "version": package.version,
                "ecosystem": package.ecosystem,
                "advisory": str(vuln.get("id") or ""),
                "aliases": [str(alias) for alias in vuln.get("aliases") or []][:6],
                "summary": str(vuln.get("summary") or "")[:400],
                "fixed": fixed[:6],
                "cwe": "CWE-1395",
                "advice": (
                    f"Обновите {package.name} до {fixed[0]} или новее."
                    if fixed
                    else f"Обновите {package.name} до исправленной версии."
                ),
            }
        )
    return findings


def query(packages: list[Package], *, transport=None, timeout: float = 6.0) -> tuple[list[dict], str]:
    """Return (findings, status). ``status`` is ``checked`` or a typed failure code."""
    packages = [package for package in packages if package.version]
    if not packages:
        return [], "no_pinned_dependencies"
    transport = transport or _http_json
    try:
        batch = transport(
            "POST", OSV_BATCH, {"queries": [package.as_query() for package in packages]}, timeout
        )
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        return [], f"advisory_lookup_unavailable:{type(error).__name__}"
    results = batch.get("results") if isinstance(batch, dict) else None
    if not isinstance(results, list) or len(results) != len(packages):
        return [], "advisory_lookup_malformed"

    wanted: dict[str, Package] = {}
    for package, result in zip(packages, results):
        for vuln in (result or {}).get("vulns") or []:
            identifier = str(vuln.get("id") or "")
            if identifier:
                wanted.setdefault(identifier, package)
    findings: list[dict] = []
    details: dict[str, dict] = {}
    for identifier in list(wanted)[:8]:
        try:
            details[identifier] = transport("GET", OSV_VULN + identifier, None, timeout)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            # The batch already proved the version is affected; a missing summary is not a reason
            # to drop the finding.
            details[identifier] = {"id": identifier}
    for identifier, package in wanted.items():
        findings.extend(evaluate([details.get(identifier, {"id": identifier})], package))
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    findings.sort(key=lambda item: (severity_order.get(item["severity"], 4), item["package"]))
    return findings, "checked"


def parse_requirements(text: str) -> list[Package]:
    packages: list[Package] = []
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-") or "://" in line:
            continue
        line = line.split(";", 1)[0].strip()
        if "==" not in line:
            continue
        name, _, version = line.partition("==")
        name = name.strip().split("[", 1)[0]
        version = version.strip().split(" ", 1)[0]
        if name and version:
            packages.append(Package("pypi", name, version))
    return packages


def parse_package_json(text: str) -> list[Package]:
    try:
        data = json.loads(text or "{}")
    except ValueError:
        return []
    packages: list[Package] = []
    for section in ("dependencies", "devDependencies"):
        for name, version in (data.get(section) or {}).items():
            value = str(version).lstrip("^~=v")
            if value and value[0].isdigit():
                packages.append(Package("npm", str(name), value))
    return packages


def parse_pyproject(text: str) -> list[Package]:
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - Python 3.11+ is the runtime
        return []
    try:
        data = tomllib.loads(text or "")
    except ValueError:
        return []
    packages: list[Package] = []
    for item in (data.get("project") or {}).get("dependencies") or []:
        line = str(item).strip()
        if "==" not in line:
            continue
        name, _, version = line.partition("==")
        name = name.strip().split("[", 1)[0].split(" ", 1)[0]
        version = version.strip().split(" ", 1)[0].split(";", 1)[0]
        if name and version:
            packages.append(Package("pypi", name, version))
    return packages
