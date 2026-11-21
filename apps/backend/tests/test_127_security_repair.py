"""Scenario F on a deliberately vulnerable local project — find, explain, fix, re-check.

The scan is the product's own rule engine (`app.security_assessment.audit`), not a mock. The fix is
scripted here on purpose: the point of this test is that detection and re-verification are real and
that a clean scan after the fix is produced by the same engine that reported the defect. A fixing
conversation driven by a real model is a LIVE claim and is not made here.
"""

from __future__ import annotations

import pytest

from app.security_assessment.audit import scan_files, report
from app.security_assessment.scope import ScopeError, SecurityScope, assert_target_allowed

VULNERABLE = {
    "app/db.py": (
        "def find(name, cursor):\n"
        "    cursor.execute(f\"SELECT * FROM users WHERE name = '{name}'\")\n"
        "    return cursor.fetchall()\n"
    ),
    "app/config.py": 'API_KEY = "sk-live-1234567890abcdef"\nTIMEOUT = 30\n',
    "app/http.py": (
        "import requests\n"
        "def get(url):\n"
        "    return requests.get(url, verify=False, timeout=10)\n"
    ),
    "app/run.py": (
        "import subprocess\n"
        "def run(user_input):\n"
        "    subprocess.run(user_input, shell=True)\n"
        "    eval(user_input)\n"
        "    app.run(debug=True)\n"
    ),
}

FIXED = {
    "app/db.py": (
        "def find(name, cursor):\n"
        "    cursor.execute(\"SELECT * FROM users WHERE name = ?\", (name,))\n"
        "    return cursor.fetchall()\n"
    ),
    "app/config.py": (
        "import os\n"
        "API_KEY = os.environ[\"API_KEY\"]\n"
        "TIMEOUT = 30\n"
    ),
    "app/http.py": (
        "import requests\n"
        "def get(url):\n"
        "    return requests.get(url, verify=True, timeout=10)\n"
    ),
    "app/run.py": (
        "import json\n"
        "import subprocess\n"
        "def run(argv):\n"
        "    subprocess.run(argv, shell=False, check=True)\n"
        "    payload = json.loads(argv[0])\n"
        "    return payload\n"
    ),
}


def _rules(paths: dict[str, str]) -> set[str]:
    return {finding.rule for finding in scan_files(paths)}


def test_the_engine_finds_the_planted_defects_with_their_cwe():
    findings = scan_files(VULNERABLE)
    by_rule = {finding.rule: finding for finding in findings}

    assert by_rule["sql_string_build"].cwe == "CWE-89"
    assert by_rule["sql_string_build"].severity == "high"
    assert by_rule["sql_string_build"].file == "app/db.py"
    assert by_rule["hardcoded_secret"].cwe == "CWE-798"
    assert "sk-live-1234567890abcdef" not in by_rule["hardcoded_secret"].evidence, (
        "a secret scanner that repeats the secret in its report is a second leak"
    )
    assert by_rule["tls_verify_disabled"].cwe == "CWE-295"
    assert by_rule["shell_true"].cwe == "CWE-78"
    assert by_rule["eval_dynamic"].cwe == "CWE-95"
    assert by_rule["debug_enabled"].cwe == "CWE-489"
    assert all(finding.advice for finding in findings), "a finding without a fix is not actionable"


def test_every_planted_defect_is_gone_after_the_fix_and_the_engine_is_the_same_one():
    before = _rules(VULNERABLE)
    assert {"sql_string_build", "hardcoded_secret", "tls_verify_disabled", "shell_true"} <= before

    after = _rules(FIXED)
    assert not after & {"sql_string_build", "hardcoded_secret", "tls_verify_disabled", "shell_true"}, after
    assert after == set(), after


def test_the_recheck_is_a_real_second_scan_not_a_cached_verdict():
    assert scan_files(VULNERABLE), "the first scan must report something"
    # The same engine on the fixed tree returns nothing; then the vulnerable tree again reports.
    assert scan_files(FIXED) == []
    assert scan_files(VULNERABLE)


def test_the_report_counts_by_severity_and_never_claims_clean_when_it_is_not():
    findings = [finding.as_json() for finding in scan_files(VULNERABLE)]
    payload = report(
        findings, files_scanned=len(VULNERABLE), packages_checked=0, advisories="not_checked"
    )
    assert sum(payload["counts"].values()) >= 6
    assert payload["counts"]["high"] >= 3
    assert payload["blocking"] > 0
    assert payload["ok"] is False, "a tree with high findings must not be called clean"


def test_a_scope_refuses_a_target_outside_the_workspace():
    scope = SecurityScope(targets=["C:/work/project"], authorized=True)
    assert_target_allowed(scope, "C:/work/project/app/db.py")
    with pytest.raises(ScopeError):
        assert_target_allowed(scope, "C:/Windows/System32/drivers/etc/hosts")
