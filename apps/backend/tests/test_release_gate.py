"""The release gate (A25): its self-test is exercised here, and its refusal paths are pinned.

The full ``candidate`` run needs real artifacts and a trusted host; what this file proves is that
the verifier's logic is wired into the suite and that it refuses rather than passes when it cannot
prove something — no installer, no crypto library, or a publish request.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "release-gate.py"


def run_gate(*args: str) -> subprocess.CompletedProcess:
    assert SCRIPT.is_file(), f"missing release gate: {SCRIPT}"
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, check=False)


def test_the_release_gate_self_test_passes():
    pytest.importorskip("cryptography")  # the Ed25519 verifier needs it
    result = run_gate("--self-test")
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "self-test PASS" in output


def test_the_release_gate_refuses_to_publish_here():
    result = run_gate("publish")
    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "not enabled" in output


def test_a_missing_installer_is_a_hard_failure():
    result = run_gate("verify-artifact", "--installer", "definitely-absent-setup.exe")
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert "installer not found" in output
