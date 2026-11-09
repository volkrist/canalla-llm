"""Controller glue for an authorized security assessment.

The analysis runs over text the product read through its own permission-checked host tools:
`list_directory` finds the files, `read_file` returns their content, and only then does
`app.security.audit` look at the text. The backend never opens a user file directly, so the
computer permission boundary is the same one every other action obeys.

The result is stored on the task (`verification["security"]`) with the scope that produced it, so
the UI can show what was checked, what was found and whether the fix was re-tested.
"""

from __future__ import annotations

from ...models import now
from ..contracts import ToolError
from .journal import append_event

MAX_READS = 45
MAX_READ_BYTES = 200_000
MANIFESTS = ("requirements.txt", "pyproject.toml", "package.json", "setup.py")
PROJECT_DIRS = ("src", "app", "lib", "tests", "backend", "server")


def _text_of(output) -> str:
    if not isinstance(output, dict):
        return ""
    return str(output.get("text") or output.get("stdout") or "")


def _listing(output) -> list[str]:
    names = []
    for line in _text_of(output).splitlines():
        name = line.strip().rstrip("/\\")
        if name and len(name) < 200 and name not in {".", ".."}:
            names.append(name)
    return names


def mark_dirty_row(row) -> bool:
    """Mark the stored assessment stale after a write. Returns False when no assessment applies."""
    if not (row.facts or {}).get("security_scope"):
        return False
    verification = dict(row.verification or {})
    security = dict(verification.get("security") or {})
    if security:
        security["fresh"] = False
        verification["security"] = security
        row.verification = verification
    facts = dict(row.facts or {})
    facts["security_dirty"] = True
    row.facts = facts
    return True


class SecurityAssessment:
    """One assessment round. Two rounds (initial + retest) are enough for a fix/verify cycle."""

    def __init__(self, controller):
        self.controller = controller

    def scope(self, context, prompt: str):
        from ...database import SessionLocal
        from ...security_assessment.scope import ScopeError, SecurityScope, build_scope
        from ..models import LocalTask

        task_id = getattr(context, "task_id", None)
        if task_id:
            with SessionLocal() as db:
                row = db.get(LocalTask, task_id)
                stored = (row.facts or {}).get("security_scope") if row else None
                if stored:
                    return SecurityScope.from_json(stored)
        roots = list(getattr(getattr(context, "settings", None), "workspace_roots", None) or [])
        try:
            scope = build_scope(prompt, roots)
        except ScopeError as error:
            if task_id:
                with SessionLocal() as db:
                    row = db.get(LocalTask, task_id)
                    if row is not None:
                        facts = dict(row.facts or {})
                        facts["security_scope_error"] = {
                            "code": error.code,
                            "message": error.message,
                        }
                        row.facts = facts
                        db.commit()
            raise ToolError(error.code) from None
        if task_id:
            with SessionLocal() as db:
                row = db.get(LocalTask, task_id)
                if row is not None:
                    facts = dict(row.facts or {})
                    facts["security_scope"] = scope.to_json()
                    row.facts = facts
                    db.commit()
        return scope

    def _row(self, context):
        task_id = getattr(context, "task_id", None)
        if not task_id:
            return None
        from ...database import SessionLocal
        from ..models import LocalTask

        with SessionLocal() as db:
            return db.get(LocalTask, task_id)

    async def _read(self, context, notes, path: str, purpose: str):
        output = await self.controller_run(context, notes, "read_file", {"path": path, "purpose": purpose})
        text = _text_of(output)
        if not text:
            return ""
        first, _, body = text.partition("\n")
        # `read_file` answers `sha256=<hex>` then the content; a host that answers plain content
        # is still usable, it just has no digest header.
        content = body if first.startswith("sha256=") else text
        return content[:MAX_READ_BYTES]

    async def controller_run(self, context, notes, name, arguments):
        # One call path for both origins: the executor records it, the task journal sees it, and a
        # refusal is a typed error rather than a silent empty report.
        return await self.controller._run(name, arguments, context, notes, origin="server_policy")

    async def assess(self, context, notes, prompt: str, *, reason: str = "initial") -> dict:
        from ...security_assessment import advisories, audit
        from ...security_assessment.scope import assert_check_allowed

        scope = self.scope(context, prompt)
        assert_check_allowed(scope, "dependencies")
        assert_check_allowed(scope, "static")
        root = scope.targets[0]
        listing = _listing(
            await self.controller_run(
                context, notes, "list_directory", {"path": root, "purpose": "security inventory"}
            )
        )
        files: dict[str, str] = {}
        scanned_names: list[str] = []
        skipped: list[str] = []

        def full(name: str) -> str:
            return root.rstrip("\\/") + "\\" + name

        # One level into the usual project directories, so a `src/app` package is not invisible.
        candidates: list[tuple[str, str]] = [(name, full(name)) for name in listing]
        for directory in [name for name in listing if name.lower() in PROJECT_DIRS]:
            inner = _listing(
                await self.controller_run(
                    context,
                    notes,
                    "list_directory",
                    {"path": full(directory), "purpose": "security inventory"},
                )
            )
            candidates.extend((f"{directory}/{name}", full(directory) + "\\" + name) for name in inner)

        manifest_candidates = [
            (name, path) for name, path in candidates if name.rsplit("/", 1)[-1] in MANIFESTS
        ]
        source_candidates = [(name, path) for name, path in candidates if "/" in name or "." in name]

        packages: list = []
        for name, path in manifest_candidates[:6]:
            text = await self._read(context, notes, path, f"security review of {name}")
            if not text:
                skipped.append(name)
                continue
            files[name] = text
            if name.endswith("requirements.txt"):
                packages.extend(advisories.parse_requirements(text))
            elif name.endswith("pyproject.toml"):
                packages.extend(advisories.parse_pyproject(text))
            elif name.endswith("package.json"):
                packages.extend(advisories.parse_package_json(text))

        source_reads = 0
        for name, path in source_candidates:
            if source_reads >= MAX_READS:
                break
            base = name.rsplit("/", 1)[-1]
            if not (audit.is_source(base) or audit.is_config(base)):
                continue
            text = await self._read(context, notes, path, f"security review of {name}")
            source_reads += 1
            if not text:
                skipped.append(name)
                continue
            files[name] = text
            scanned_names.append(name)

        transport = getattr(context, "advisory_transport", None)
        dependency_findings, status = advisories.query(packages, transport=transport)
        static_findings = [finding.as_json() for finding in audit.scan_files(files)]
        findings = audit.sort_findings(static_findings + dependency_findings)
        result = audit.report(
            findings,
            files_scanned=len(scanned_names),
            packages_checked=len(packages),
            advisories=status,
            skipped=skipped,
        )
        result.update(
            {
                "reason": reason,
                "assessed_at": now().isoformat(),
                "scope": scope.to_json(),
                "files": scanned_names[:40],
                "packages": [
                    {"ecosystem": package.ecosystem, "name": package.name, "version": package.version}
                    for package in packages[:40]
                ],
            }
        )
        self.record(context, result)
        return result

    def record(self, context, result: dict) -> None:
        task_id = getattr(context, "task_id", None)
        if not task_id:
            return
        from ...database import SessionLocal
        from ..models import LocalTask

        with SessionLocal() as db:
            row = db.get(LocalTask, task_id)
            if not row:
                return
            verification = dict(row.verification or {})
            verification["security"] = result
            row.verification = verification
            facts = dict(row.facts or {})
            facts["security_last_assessment_at"] = result.get("assessed_at")
            facts.pop("security_dirty", None)
            row.facts = facts
            row.updated_at = now()
            append_event(
                db,
                row.id,
                "SECURITY_ASSESSED",
                {
                    "reason": result.get("reason", ""),
                    "findings": str(len(result.get("findings") or [])),
                    "blocking": str(result.get("blocking", 0)),
                },
                getattr(context, "secrets", ()),
            )
            db.commit()

    def mark_dirty(self, context) -> None:
        """A write after an assessment means the report no longer describes the code."""
        task_id = getattr(context, "task_id", None)
        if not task_id:
            return
        from ...database import SessionLocal
        from ..models import LocalTask

        with SessionLocal() as db:
            row = db.get(LocalTask, task_id)
            if not row:
                return
            if not mark_dirty_row(row):
                return
            row.updated_at = now()
            db.commit()

    def verdict(self, row) -> dict:
        """Final-review input: a fresh, clean assessment or a typed reason why it is not one."""
        facts = row.facts or {}
        if facts.get("security_scope_error"):
            error = facts["security_scope_error"]
            return {"required": True, "ok": False, "reason": str(error.get("code") or "security_scope")}
        if not facts.get("security_scope"):
            return {"required": False, "ok": True}
        verification = row.verification or {}
        security = verification.get("security") or {}
        if not security:
            return {"required": True, "ok": False, "reason": "security_assessment_required"}
        if facts.get("security_dirty"):
            return {"required": True, "ok": False, "reason": "security_retest_required"}
        advisories = str(security.get("advisories") or "")
        if advisories.startswith("advisory_lookup_unavailable") or advisories.startswith(
            "advisory_lookup_malformed"
        ):
            return {"required": True, "ok": False, "reason": "security_advisory_lookup_unavailable"}
        if not security.get("ok"):
            return {"required": True, "ok": False, "reason": "security_open_findings"}
        return {"required": True, "ok": True, "reason": ""}


def summary_for_model(result: dict) -> str:
    """A compact, non-secret summary the model can act on."""
    findings = result.get("findings") or []
    lines = [
        f"SECURITY ASSESSMENT ({result.get('reason')}): files={result.get('files_scanned')} "
        f"packages={result.get('packages_checked')} advisories={result.get('advisories')} "
        f"blocking={result.get('blocking')}"
    ]
    for item in findings[:12]:
        location = f"{item.get('file')}:{item.get('line')}" if item.get("file") else ""
        lines.append(
            f"- [{item.get('severity')}] {item.get('rule')} {location} "
            f"{item.get('advisory') or ''} {str(item.get('summary') or item.get('advice') or '')[:160]}"
        )
    if not findings:
        lines.append("- no confirmed high-risk findings")
    return "\n".join(lines)
