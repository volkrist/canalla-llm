"""The stale-sidecar guard: a build must not ship a backend from an older source tree.

This exists because it already happened. The 1.2.0 candidate was built on 24 September while its
packaged backend had been frozen on 23 September, before the version bump and before that release's
own Tor routing work: the installer said 1.2.0 and the backend answered `/health` with 1.1.0. The
bundle step checked that a sidecar *existed*, never where it came from, so nothing failed.

`scripts/backend-sidecar-stamp.py` closes that gap, and these tests pin its contract: a current
stamp passes, and every way of being stale fails with `BACKEND_SIDECAR_STALE` rather than a warning,
because a warning in a build log is a warning nobody reads.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
TOOL = REPO / "scripts" / "backend-sidecar-stamp.py"
REAL_BACKEND = REPO / "apps" / "backend"
REAL_STAMP = REPO / "apps" / "desktop" / "src-tauri" / "sidecar" / "alex-backend" / "build-stamp.json"

STALE = "BACKEND_SIDECAR_STALE"


def backend_tree(root: Path, *, version: str = "1.2.0", marker: str = "one") -> Path:
    """A miniature backend tree with the same shape the digest walks."""
    (root / "app").mkdir(parents=True)
    (root / "app" / "product.py").write_text(
        f'"""Identity."""\n\nPRODUCT = "alex-llm"\nVERSION = "{version}"\n', encoding="utf-8"
    )
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "app" / "service.py").write_text(f"MARKER = {marker!r}\n", encoding="utf-8")
    (root / "alembic" / "versions").mkdir(parents=True)
    (root / "alembic" / "versions" / "0015_x.py").write_text("revision = '0015'\n", encoding="utf-8")
    (root / "alembic.ini").write_text("[alembic]\n", encoding="utf-8")
    (root / "alex-backend.spec").write_text("# spec\n", encoding="utf-8")
    (root / "pyproject.toml").write_text('[project]\nversion = "1.2.0"\n', encoding="utf-8")
    return root


def sidecar_dir(root: Path, *, payload: bytes = b"packaged-backend-binary") -> Path:
    directory = root / "sidecar"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "alex-backend.exe").write_bytes(payload)
    return directory


def run(action: str, backend: Path, stamp: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOOL), action, "--backend", str(backend), "--stamp", str(stamp)],
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_a_freshly_built_sidecar_passes(tmp_path):
    """The positive control: write the stamp, then accept it."""
    backend = backend_tree(tmp_path / "backend")
    stamp = sidecar_dir(tmp_path / "staged") / "build-stamp.json"
    assert run("write", backend, stamp).returncode == 0
    checked = run("check", backend, stamp)
    assert checked.returncode == 0, checked.stderr


def test_the_stamp_records_the_product_version_and_the_binary(tmp_path):
    backend = backend_tree(tmp_path / "backend", version="1.2.0")
    stamp = sidecar_dir(tmp_path / "staged") / "build-stamp.json"
    run("write", backend, stamp)
    written = json.loads(stamp.read_text(encoding="utf-8"))
    assert written["product_version"] == "1.2.0"
    assert written["source_files"] > 0
    assert len(written["source_digest"]) == 64
    assert written["backend_exe_sha256"]


def test_a_changed_product_version_is_refused(tmp_path):
    backend = backend_tree(tmp_path / "backend", version="1.1.0")
    stamp = sidecar_dir(tmp_path / "staged") / "build-stamp.json"
    run("write", backend, stamp)

    (backend / "app" / "product.py").write_text('PRODUCT = "alex-llm"\nVERSION = "1.2.0"\n', encoding="utf-8")
    checked = run("check", backend, stamp)
    assert checked.returncode == 3
    assert STALE in checked.stderr
    assert "product_version" in checked.stderr


def test_changed_backend_source_is_refused(tmp_path):
    """The exact failure: the tree moved on, the packaged backend did not."""
    backend = backend_tree(tmp_path / "backend")
    stamp = sidecar_dir(tmp_path / "staged") / "build-stamp.json"
    run("write", backend, stamp)

    (backend / "app" / "service.py").write_text("MARKER = 'two'\n", encoding="utf-8")
    checked = run("check", backend, stamp)
    assert checked.returncode == 3
    assert STALE in checked.stderr
    assert "source_digest" in checked.stderr


def test_an_older_sidecar_binary_is_refused(tmp_path):
    """A stamp cannot describe a binary that was replaced after it was written."""
    backend = backend_tree(tmp_path / "backend")
    staged = sidecar_dir(tmp_path / "staged")
    stamp = staged / "build-stamp.json"
    run("write", backend, stamp)

    (staged / "alex-backend.exe").write_bytes(b"an older packaged backend")
    checked = run("check", backend, stamp)
    assert checked.returncode == 3
    assert STALE in checked.stderr
    assert "changed_since_the_stamp" in checked.stderr


def test_a_missing_stamp_is_refused(tmp_path):
    backend = backend_tree(tmp_path / "backend")
    stamp = sidecar_dir(tmp_path / "staged") / "build-stamp.json"
    checked = run("check", backend, stamp)
    assert checked.returncode == 3
    assert STALE in checked.stderr
    assert "stamp_missing" in checked.stderr


def test_line_endings_alone_do_not_change_the_digest(tmp_path):
    """A Windows and a Linux checkout of one revision must not disagree."""
    backend = backend_tree(tmp_path / "backend")
    stamp = sidecar_dir(tmp_path / "staged") / "build-stamp.json"
    run("write", backend, stamp)
    before = json.loads(stamp.read_text(encoding="utf-8"))["source_digest"]

    target = backend / "app" / "service.py"
    # Force CRLF regardless of how the file was written: normalise to LF first, then expand, so the
    # test cannot accidentally produce `\r\r\n` and blame the digest.
    target.write_bytes(target.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert b"\r\n" in target.read_bytes()
    run("write", backend, stamp)
    assert json.loads(stamp.read_text(encoding="utf-8"))["source_digest"] == before
    assert run("check", backend, stamp).returncode == 0


def test_tests_are_not_part_of_the_digest(tmp_path):
    """Tests are not shipped, so changing one must not force a sidecar rebuild."""
    backend = backend_tree(tmp_path / "backend")
    stamp = sidecar_dir(tmp_path / "staged") / "build-stamp.json"
    run("write", backend, stamp)
    before = json.loads(stamp.read_text(encoding="utf-8"))["source_digest"]

    (backend / "tests").mkdir()
    (backend / "tests" / "test_whatever.py").write_text("def test_x():\n    assert True\n")
    run("write", backend, stamp)
    assert json.loads(stamp.read_text(encoding="utf-8"))["source_digest"] == before


@pytest.mark.skipif(
    not REAL_STAMP.is_file(),
    reason="no staged sidecar in this checkout, so there is nothing to attest",
)
def test_the_staged_sidecar_in_this_repository_is_current():
    """The real tree: whoever bundles this must not ship a stale backend."""
    assert REAL_BACKEND.is_dir(), REAL_BACKEND
    checked = run("check", REAL_BACKEND, REAL_STAMP)
    assert checked.returncode == 0, checked.stderr
