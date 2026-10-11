"""Packaged-artifact guard: the production Gateway URL must be compiled into the client.

The 1.2.0 installer was built without ``ALEX_GATEWAY_URL``; a fresh install then showed an empty
«Адрес Gateway» field and a disabled «Подключить», so the client could not enroll without a human
typing the address. The guard belongs to the build path — and it is exercised here so a build
that silently loses the URL fails the suite instead of shipping.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "verify-packaged-gateway-url.py"


def test_the_packaged_gateway_url_guard_detects_and_accepts():
    """Both directions: the 1.2.0 signature (updater URL only) must fail, a baked URL must pass."""
    assert SCRIPT.is_file(), f"missing guard: {SCRIPT}"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--self-test"],
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "self-test PASS" in output
