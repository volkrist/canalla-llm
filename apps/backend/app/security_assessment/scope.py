"""The security-assessment contract: what Canalla may check, and what it must refuse.

An assessment is only legitimate when the target is something the user actually owns or is
authorized to test. A typed scope is therefore built *before* any check runs, and every check is
validated against it. "The user typed an IP" is not authorization, and a web page that mentions a
target never widens the scope.

The scope is stored with the task (`facts["security_scope"]`) so it survives a relaunch and can be
shown to the user, and so a later retest is judged against the same contract.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

CHECKS = {
    "dependencies",  # known vulnerable package versions
    "static",  # source patterns and configuration reviewed by rules
    "config",  # framework and deployment configuration
    "tests",  # the project's own test suite after a fix
}

FORBIDDEN = {
    "exploit",
    "auth_bypass",
    "data_exfiltration",
    "denial_of_service",
    "credential_attack",
    "persistence",
    "lateral_movement",
    "out_of_scope_target",
}

ENVIRONMENTS = {"lab", "production"}

# Phrases that turn an explicit external target into an authorized one. They are deliberately
# narrow: the user must say that the target is theirs or that testing it is allowed.
AUTHORIZED_PHRASES = (
    r"(?i)\bразрешено\s+(?:тестир|провер)",
    r"(?i)\bя\s+владелец",
    r"(?i)\b(?:i|we)\s+own\b",
    r"(?i)\bauthori[sz]ed\s+to\s+test",
    r"(?i)\bpermission\s+to\s+test",
    r"(?i)\bin\s+scope\b",
    r"(?i)\bв\s+рамках\s+(?:разреш|scope)",
)

SECURITY_INTENT = re.compile(
    r"(?i)(audit|security\s+assessment|security\s+review|vulnerab|уязвим|"
    r"проверь\s+.*(?:безопас|уязвим)|security\s+scan|аудит\s+безопас|"
    r"appsec|devsecops|cve-\d{4}-\d+|security\s+advisories?)"
)

PRODUCTION_HINT = re.compile(r"(?i)\bproduction\b|\bпрод(?:акшн|е)\b|\bprod\b")


class ScopeError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(code)


@dataclass
class SecurityScope:
    targets: list[str] = field(default_factory=list)
    target_kind: str = "workspace"
    authorized: bool = False
    authorization: str = ""
    checks: list[str] = field(default_factory=lambda: sorted(CHECKS))
    forbidden: list[str] = field(default_factory=lambda: sorted(FORBIDDEN))
    environment: str = "lab"
    external_targets: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data) -> SecurityScope:
        data = data or {}
        return cls(
            targets=list(data.get("targets") or []),
            target_kind=str(data.get("target_kind") or "workspace"),
            authorized=bool(data.get("authorized")),
            authorization=str(data.get("authorization") or ""),
            checks=list(data.get("checks") or sorted(CHECKS)),
            forbidden=list(data.get("forbidden") or sorted(FORBIDDEN)),
            environment=str(data.get("environment") or "lab"),
            external_targets=list(data.get("external_targets") or []),
            notes=list(data.get("notes") or []),
        )


def looks_like_security(prompt: str) -> bool:
    return bool(SECURITY_INTENT.search(prompt or ""))


def is_authorized(prompt: str) -> bool:
    text = prompt or ""
    return any(re.search(pattern, text) for pattern in AUTHORIZED_PHRASES)


def _norm(path: str) -> str:
    return str(Path(path).expanduser().resolve()).rstrip("\\/")


def _inside(path: str, root: str) -> bool:
    try:
        Path(path).relative_to(Path(root))
        return True
    except ValueError:
        return False


def build_scope(prompt: str, roots: list[str], *, known_paths: list[str] | None = None) -> SecurityScope:
    """Derive the contract from the request.

    Filesystem targets come from the allowed workspace roots and from paths named in the prompt;
    a path outside every allowed root is refused rather than clipped. External (http/https)
    targets are recorded but never authorized implicitly: an active check against them would need
    a phrase that states ownership or permission, and active network checks are not implemented at
    all in this release.
    """
    roots = [root for root in (roots or []) if root]
    text = prompt or ""
    scope = SecurityScope()
    if not roots:
        raise ScopeError(
            "security_scope_missing",
            "Нет разрешённой рабочей папки: проверять нечего и негде.",
        )
    scope.targets = [_norm(root) for root in roots]
    scope.notes.append("Цели ограничены разрешёнными рабочими папками.")

    named = re.findall(r"[A-Za-z]:\\[^\s\"'<>|]+|/(?:[\w.\-]+/)+[\w.\-]+", text)
    for candidate in named:
        resolved = _norm(candidate)
        if not any(_inside(resolved, root) for root in scope.targets):
            raise ScopeError(
                "security_target_out_of_scope",
                f"Путь {candidate} находится вне разрешённой рабочей папки.",
            )

    external = re.findall(r"https?://[^\s\"'<>]+", text)
    if external:
        scope.external_targets = sorted({url.rstrip(".,)") for url in external})
        if not is_authorized(text):
            raise ScopeError(
                "security_target_not_authorized",
                "Внешняя цель указана без подтверждения права на проверку. "
                "Активные проверки по ней не выполняются.",
            )
        scope.notes.append(
            "Внешняя цель указана с подтверждением права; активные сетевые проверки в этой "
            "версии не выполняются."
        )

    scope.authorized = True
    scope.authorization = "workspace_owner"
    scope.environment = "production" if PRODUCTION_HINT.search(text) else "lab"
    if scope.environment == "production":
        scope.notes.append("Окружение помечено как production: изменения только в коде/конфигурации.")
    scope.target_kind = "workspace"
    if known_paths:
        for path in known_paths:
            resolved = _norm(path)
            if not any(_inside(resolved, root) for root in scope.targets):
                raise ScopeError(
                    "security_target_out_of_scope",
                    f"Путь {path} находится вне разрешённой рабочей папки.",
                )
    return scope


def assert_check_allowed(scope: SecurityScope, check: str) -> None:
    if check not in CHECKS:
        raise ScopeError("security_check_unknown", f"Неизвестная проверка: {check}")
    if check not in scope.checks:
        raise ScopeError("security_check_not_allowed", f"Проверка {check} не входит в scope")


def assert_target_allowed(scope: SecurityScope, path: str) -> None:
    resolved = _norm(path)
    if not any(_inside(resolved, root) for root in scope.targets):
        raise ScopeError("security_target_out_of_scope", f"{path} вне разрешённого scope проверки")
