"""Authorized security assessment: scope, advisories, static review, retest.

The workflow is deliberately evidence-first: the scope is decided before any check, every read goes
through the product's permission-checked host tools, and a task cannot close until an assessment has
been re-run on the changed files.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error

import pytest
from sqlalchemy import select

from app.database import SessionLocal
from app.models import Message
from app.security_assessment import advisories, audit
from app.security_assessment.scope import (
    ScopeError,
    assert_check_allowed,
    build_scope,
    looks_like_security,
)
from app.tools.executor import ExecutionContext, ToolExecutor
from app.tools.local import machine
from app.tools.models import LocalTask
from app.tools.orchestrator import ToolOrchestrator
from app.tools.policy import ToolLimits, WebSettings
from app.tools.registry import make_registry

PROMPT = "Проверь мой проект на известные уязвимости, исправь найденные проблемы и проверь результат."


# ------------------------------------------------------------------------------- scope contract


def test_an_authorized_workspace_is_the_only_scope(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    scope = build_scope(PROMPT, [str(root)])
    assert scope.authorized is True
    assert scope.targets == [str(root.resolve())]
    assert "out_of_scope_target" in scope.forbidden
    assert scope.environment == "lab"


def test_a_named_path_outside_the_workspace_is_refused(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    with pytest.raises(ScopeError) as error:
        build_scope(f"{PROMPT} Проверь C:\\Windows\\System32", [str(root)])
    assert error.value.code == "security_target_out_of_scope"


def test_a_typed_external_target_is_not_authorization(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    with pytest.raises(ScopeError) as error:
        build_scope(f"{PROMPT} Проверь https://example.com", [str(root)])
    assert error.value.code == "security_target_not_authorized"


def test_an_explicitly_owned_external_target_is_recorded_but_not_probed(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    scope = build_scope(f"{PROMPT} Разрешено тестировать: https://example.com", [str(root)])
    assert scope.external_targets == ["https://example.com"]
    assert any("не выполняются" in note for note in scope.notes)


def test_no_allowed_workspace_means_no_assessment():
    with pytest.raises(ScopeError) as error:
        build_scope(PROMPT, [])
    assert error.value.code == "security_scope_missing"


def test_a_check_outside_the_contract_is_refused(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    scope = build_scope(PROMPT, [str(root)])
    with pytest.raises(ScopeError) as error:
        assert_check_allowed(scope, "exploit")
    assert error.value.code == "security_check_unknown"


def test_security_intent_is_recognized_but_a_normal_request_is_not():
    assert looks_like_security(PROMPT)
    assert looks_like_security("Check CVE-2024-3094 in our dependencies")
    assert not looks_like_security("Создай калькулятор на Python")


# ----------------------------------------------------------------------------- static review


def test_the_static_rules_name_file_line_and_evidence():
    text = "import subprocess\nsubprocess.run(cmd, shell=True)\n"
    findings = [finding.as_json() for finding in audit.scan_text("app.py", text)]
    assert [item["rule"] for item in findings] == ["shell_true"]
    assert findings[0]["file"] == "app.py"
    assert findings[0]["line"] == 2
    assert findings[0]["severity"] == "high"
    assert findings[0]["cwe"] == "CWE-78"
    assert "shell=True" in findings[0]["evidence"]


def test_a_secret_is_never_repeated_in_the_report():
    text = 'API_KEY = "sk-live-1234567890abcdef"\n'
    findings = [finding.as_json() for finding in audit.scan_text("config.py", text)]
    assert findings and findings[0]["rule"] == "hardcoded_secret"
    assert "sk-live-1234567890abcdef" not in findings[0]["evidence"]
    assert "***" in findings[0]["evidence"]


def test_a_placeholder_is_not_a_hardcoded_secret():
    for line in (
        'API_KEY = "your-api-key-here"',
        'API_KEY = os.environ["API_KEY"]',
        "token = process.env.TOKEN",
    ):
        assert not [f for f in audit.scan_text("x.py", line) if f.rule == "hardcoded_secret"]


def test_test_code_is_a_low_risk_finding_not_a_blocker():
    findings = [f.as_json() for f in audit.scan_text("tests/test_x.py", "eval(user_input)")]
    assert findings and findings[0]["severity"] == "low"


def test_a_literal_eval_is_not_a_finding():
    assert not [f for f in audit.scan_text("x.py", 'eval("1 + 1")')]


def test_the_report_blocks_only_on_high_risk():
    low = audit.report(
        [{"severity": "low", "rule": "x"}], files_scanned=1, packages_checked=0, advisories="checked"
    )
    high = audit.report(
        [{"severity": "high", "rule": "x"}], files_scanned=1, packages_checked=0, advisories="checked"
    )
    assert low["ok"] is True and low["blocking"] == 0
    assert high["ok"] is False and high["blocking"] == 1


# ------------------------------------------------------------------------------- advisories


def test_requirements_are_parsed_only_when_pinned():
    packages = advisories.parse_requirements(
        "Flask==2.0.0\nrequests>=2\n# comment\n-r other.txt\nuvicorn==0.30.1 ; python_version>'3.9'\n"
    )
    assert [(p.name, p.version) for p in packages] == [("Flask", "2.0.0"), ("uvicorn", "0.30.1")]


def _transport(vuln_id="GHSA-aaaa", fixed="2.3.2"):
    calls = []

    def fake(method, url, body, timeout):
        calls.append((method, url))
        if method == "POST":
            queries = body["queries"]
            return {
                "results": [
                    {"vulns": [{"id": vuln_id}]} if query["package"]["name"] == "flask" else {}
                    for query in queries
                ]
            }
        return {
            "id": vuln_id,
            "summary": "Flask affected before 2.3.2",
            "aliases": ["CVE-2023-30861"],
            "severity": [{"type": "CVSS_V3", "score": "7.5"}],
            "database_specific": {"severity": "HIGH"},
            "affected": [
                {
                    "package": {"name": "flask", "ecosystem": "PyPI"},
                    "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": fixed}]}],
                }
            ],
        }

    return fake, calls


def test_an_advisory_is_matched_to_the_exact_version():
    fake, calls = _transport()
    findings, status = advisories.query([advisories.Package("pypi", "flask", "2.0.0")], transport=fake)
    assert status == "checked"
    assert len(findings) == 1
    assert findings[0]["advisory"] == "GHSA-aaaa"
    assert findings[0]["aliases"] == ["CVE-2023-30861"]
    assert findings[0]["fixed"] == ["2.3.2"]
    assert findings[0]["severity"] == "high"
    assert calls[0][0] == "POST" and calls[1][0] == "GET"


def test_an_exploited_cve_is_marked_by_the_cisa_catalogue():
    """A CVE CISA lists as exploited in the wild outranks a theoretical one."""
    findings = [
        {
            "kind": "dependency",
            "rule": "vulnerable_dependency",
            "severity": "high",
            "package": "flask",
            "advisory": "GHSA-aaaa",
            "aliases": ["CVE-2023-30861"],
        }
    ]

    def feed(method, url, body, timeout):
        assert url.endswith("known_exploited_vulnerabilities.json")
        return {"vulnerabilities": [{"cveID": "CVE-2023-30861"}, {"cveID": "CVE-2020-0001"}]}

    state = advisories.kev_enrich(findings, transport=feed)
    assert state == "checked:1"
    assert findings[0]["known_exploited"] is True
    assert findings[0]["severity"] == "critical"


def test_an_unreachable_kev_feed_does_not_change_the_finding():
    findings = [{"severity": "high", "advisory": "CVE-2023-30861", "aliases": []}]

    def broken(method, url, body, timeout):
        raise urllib.error.URLError("offline")

    assert advisories.kev_enrich(findings, transport=broken) == "unavailable"
    assert findings[0]["severity"] == "high"
    assert "known_exploited" not in findings[0]


def test_without_cve_aliases_the_kev_feed_is_not_queried():
    assert advisories.kev_enrich([{"severity": "high", "advisory": "GHSA-x", "aliases": []}]) == (
        "not_needed"
    )


def test_an_unreachable_advisory_service_is_a_typed_failure_not_a_clean_result():
    def broken(method, url, body, timeout):
        raise urllib.error.URLError("offline")

    findings, status = advisories.query([advisories.Package("pypi", "flask", "2.0.0")], transport=broken)
    assert findings == []
    assert status.startswith("advisory_lookup_unavailable")


# ------------------------------------------------------------------------- end-to-end workflow


def _call(name, arguments, call_id):
    return {
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ]
    }


def _context(user_id, chat_id, generation_id, root, prompt):
    events = []

    async def emit(event, value):
        events.append((event, value))

    context = ExecutionContext(
        user_id,
        chat_id,
        generation_id,
        ToolLimits(max_calls=24, hard_max_calls=40),
        emit,
        mode="off",
        computer_mode="trusted",
        settings=WebSettings(computer_mode="trusted", workspace_roots=[str(root)], default_mode="off"),
        user_prompt=prompt,
        host_online=True,
    )
    return context, events


@pytest.fixture
def security_setup(client, auth, tmp_path):
    from tests.fake_host import FakeHost

    headers = auth()
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    chat_id = client.post("/chats", headers=headers, json={}).json()["id"]
    root = tmp_path / "project"
    root.mkdir()
    (root / "app.py").write_text(
        "import subprocess\n\ndef run(cmd):\n    return subprocess.run(cmd, shell=True)\n",
        encoding="utf-8",
    )
    (root / "requirements.txt").write_text("requests==2.31.0\n", encoding="utf-8")
    client.put(
        "/tools/preferences",
        headers=headers,
        json={"computer_mode": "trusted", "workspace_roots": [str(root)]},
    )
    paired = client.post("/tools/devices/pair", headers=headers, json={"platform": "windows"}).json()
    device_headers = {
        **headers,
        "X-Alex-Device-Id": paired["device_id"],
        "X-Alex-Device-Credential": paired["credential"],
    }
    client.post("/tools/devices/heartbeat", headers=device_headers)
    with SessionLocal() as db:
        message = Message(chat_id=chat_id, role="assistant", content="")
        db.add(message)
        db.commit()
        generation_id = message.id
    context, events = _context(user_id, chat_id, generation_id, root, PROMPT)

    class NoVulns:
        def __call__(self, method, url, body, timeout):
            if method == "POST":
                return {"results": [{} for _ in body["queries"]]}
            return {}

    context.advisory_transport = NoVulns()
    host = FakeHost(client, device_headers, root).start()
    try:
        yield context, events, root, host
    finally:
        host.close()


class FixPlanner:
    """Fixes the one confirmed finding, then stops."""

    supports_tools = True

    def __init__(self, root):
        self.root = root
        self.turns = 0

    async def plan_tools(self, messages, tools, usage):
        import hashlib

        self.turns += 1
        target = self.root / "app.py"
        text = target.read_text(encoding="utf-8")
        if "shell=True" in text:
            return _call(
                "patch_file",
                {
                    "path": str(target),
                    "old_text": "return subprocess.run(cmd, shell=True)",
                    "new_text": "return subprocess.run([cmd], shell=False)",
                    "expected_before_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                    "purpose": "remove shell=True",
                },
                "fix-shell",
            )
        return {"tool_calls": []}


def test_a_confirmed_finding_is_fixed_and_the_assessment_is_re_run(security_setup):
    context, _events, root, _host = security_setup
    orchestrator = ToolOrchestrator(make_registry(), ToolExecutor(make_registry()))
    asyncio.run(orchestrator.prepare(FixPlanner(root), [{"role": "user", "content": PROMPT}], 0, context, {}))

    assert "shell=True" not in (root / "app.py").read_text(encoding="utf-8")
    with SessionLocal() as db:
        row = db.scalar(select(LocalTask).where(LocalTask.id == context.task_id))
        assert row is not None
        security = (row.verification or {}).get("security") or {}
        assert security.get("reason") == "retest"
        assert security.get("ok") is True, security.get("findings")
        assert (row.facts or {}).get("security_scope")
        assert not (row.facts or {}).get("security_dirty")
        assert row.status == machine.COMPLETED


class NoFixPlanner:
    supports_tools = True

    async def plan_tools(self, messages, tools, usage):
        return {"tool_calls": []}


def test_an_unfixed_finding_keeps_the_task_failed(security_setup):
    context, _events, root, _host = security_setup
    orchestrator = ToolOrchestrator(make_registry(), ToolExecutor(make_registry()))
    asyncio.run(orchestrator.prepare(NoFixPlanner(), [{"role": "user", "content": PROMPT}], 0, context, {}))
    with SessionLocal() as db:
        row = db.scalar(select(LocalTask).where(LocalTask.id == context.task_id))
        assert row is not None
        assert row.status == machine.FAILED
        assert row.last_error == "security_open_findings"
        security = (row.verification or {}).get("security") or {}
        assert security.get("counts", {}).get("high", 0) >= 1


def test_an_out_of_scope_target_fails_the_task_instead_of_being_ignored(security_setup):
    context, _events, _root, _host = security_setup
    context.user_prompt = PROMPT + " Проверь C:\\Windows\\System32"
    orchestrator = ToolOrchestrator(make_registry(), ToolExecutor(make_registry()))
    asyncio.run(
        orchestrator.prepare(
            NoFixPlanner(), [{"role": "user", "content": context.user_prompt}], 0, context, {}
        )
    )
    with SessionLocal() as db:
        row = db.scalar(select(LocalTask).where(LocalTask.id == context.task_id))
        assert row is not None
        assert row.status == machine.FAILED
        assert row.last_error == "security_target_out_of_scope"
        assert (row.facts or {}).get("security_scope_error", {}).get("code") == (
            "security_target_out_of_scope"
        )
