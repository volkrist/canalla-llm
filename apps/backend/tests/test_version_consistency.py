"""One release version, written by hand in eight places: prove that they all agree.

Every value asserted here is a literal somebody typed, which is why a release could otherwise
ship two different versions. `tauri.conf.json` is the *effective* app version — the NSIS
installer, the `.deb` control file and the version the frontend reports all derive from it — but
`Cargo.toml` matters separately, because the desktop sends `env!("CARGO_PKG_VERSION")` to the
Gateway as `client_version`, so bumping only the config would send a stale identity.

Nothing derived is compared against itself and no test expectation is used as a source of truth:
the only anchor is `app.product.VERSION`, and a failure here means some file disagrees with it.
This check exists because the previous release had none, and `main.py` had already drifted — it
advertised one version to `FastAPI` while `/health` reported another. That specific drift is
asserted against directly by `test_fastapi_is_told_the_version_and_not_a_literal`.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from app.product import VERSION

REPO = Path(__file__).resolve().parents[3]
FRONTEND = REPO / "apps" / "desktop"
TAURI = FRONTEND / "src-tauri"

# A version the bundlers can derive metadata from: three plain numeric parts, nothing else.
RELEASE_VERSION = re.compile(r"^\d+\.\d+\.\d+$")


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _toml(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def test_the_version_is_a_plain_release_version():
    # `VERSIONWITHBUILD` for NSIS and the `.deb` control file are built from this shape.
    assert RELEASE_VERSION.match(VERSION), f"{VERSION} is not a plain x.y.z release version"


def test_the_desktop_config_carries_the_release_version():
    assert _json(TAURI / "tauri.conf.json")["version"] == VERSION


def test_the_crate_carries_the_release_version():
    # Sent to the Gateway as `client_version`, so it is not merely metadata.
    assert _toml(TAURI / "Cargo.toml")["package"]["version"] == VERSION


def test_the_crate_lock_agrees_with_the_crate():
    locked = [p for p in _toml(TAURI / "Cargo.lock")["package"] if p["name"] == "alex-llm"]
    assert len(locked) == 1, "the workspace crate must appear exactly once in Cargo.lock"
    assert locked[0]["version"] == VERSION


def test_the_frontend_manifest_carries_the_release_version():
    assert _json(FRONTEND / "package.json")["version"] == VERSION


def test_the_frontend_lock_agrees_with_the_manifest():
    lock = _json(FRONTEND / "package-lock.json")
    assert lock["version"] == VERSION
    assert lock["packages"][""]["version"] == VERSION


def test_the_backend_package_carries_the_release_version():
    assert _toml(REPO / "apps" / "backend" / "pyproject.toml")["project"]["version"] == VERSION


def test_the_gateway_package_carries_the_release_version():
    assert _toml(REPO / "apps" / "gateway" / "pyproject.toml")["project"]["version"] == VERSION


def test_the_gateway_reports_the_release_version():
    # `GET /health` and `POST /enroll` answer with this value, so a stale one is visible to every
    # client. It is a pydantic default, not imported from the backend (separate service).
    text = (REPO / "apps" / "gateway" / "gateway" / "config.py").read_text(encoding="utf-8")
    found = re.findall(r'^\s*version:\s*str\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert found == [VERSION], f"gateway config.py declares {found}, expected [{VERSION}]"


def test_fastapi_is_told_the_version_and_not_a_literal():
    # The exact drift this file exists for: the app advertised "1.1.0" as a literal while `/health`
    # answered with `VERSION`. Both must come from the one constant.
    text = (REPO / "apps" / "backend" / "app" / "main.py").read_text(encoding="utf-8")
    assert re.search(r"FastAPI\(", text), "main.py must construct the FastAPI app"
    assert re.search(r"^\s*version=VERSION,\s*$", text, re.MULTILINE), (
        "the FastAPI app must be constructed with `version=VERSION`"
    )
    assert not re.search(r"^\s*version=\"\d", text, re.MULTILINE), (
        "main.py must not hard-code a version literal"
    )
