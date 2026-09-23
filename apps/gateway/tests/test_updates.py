"""The update endpoint: public metadata only, no downgrades, no unsigned artifacts.

Every case here is offline: the manifest is a temporary file (or inline JSON), and the endpoint is
the only thing under test. No provider traffic, no compute, no client.

The signature and the digest below belong to the committed updater fixture
(`apps/desktop/src-tauri/tests/fixtures/updater/`), so what these tests serve is exactly the shape a
real release would publish - and the same bytes are parsed by the Rust gate with the updater's own
`RemoteRelease` type.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import SecretStr

from gateway import updates
from gateway.config import GatewaySettings

REPO = Path(__file__).resolve().parents[3]
UPDATER_FIXTURES = REPO / "apps" / "desktop" / "src-tauri" / "tests" / "fixtures" / "updater"
SIGNATURE = (UPDATER_FIXTURES / "canalla-update-fixture.bin.sig").read_text(encoding="utf-8").strip()
ARTIFACT_SHA256 = hashlib.sha256((UPDATER_FIXTURES / "canalla-update-fixture.bin").read_bytes()).hexdigest()
CONTRACT = Path(__file__).parent / "fixtures" / "updates-response-1.2.0.json"

WINDOWS_URL = "https://gateway.12testers.store/downloads/Canalla%20LLM_1.2.0_x64-setup.exe"
LINUX_URL = "https://gateway.12testers.store/downloads/Canalla%20LLM_1.2.0_amd64.deb"
NOTES = "Компактный интерфейс, always-ready сервисы, автообновление."

ENTRY = {"url": WINDOWS_URL, "signature": SIGNATURE, "sha256": ARTIFACT_SHA256}


def manifest(version: str = "1.2.0", *, platforms=None, **extra) -> dict:
    body = {
        "version": version,
        "channel": "stable",
        "pub_date": "2026-09-23T00:00:00Z",
        "notes": NOTES,
        "platforms": platforms
        if platforms is not None
        else {
            "windows-x86_64": {**ENTRY, "url": WINDOWS_URL},
            "linux-x86_64": {**ENTRY, "url": LINUX_URL},
        },
    }
    body.update(extra)
    return body


@pytest.fixture
def manifest_file(tmp_path):
    def write(body: dict) -> str:
        path = tmp_path / "updates.json"
        path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        return str(path)

    return write


def settings_for(path: str | None = None, inline: str | None = None) -> GatewaySettings:
    return GatewaySettings(
        app_env="test",
        database_url="sqlite:///" + ":memory:",
        jwt_secret="gateway-test-secret-" + "s" * 40,
        runpod_api_key=SecretStr("test-only-fake-runpod-key"),
        updates_manifest_path=path or "",
        updates_manifest_json=SecretStr(inline or ""),
    )


def get(client, path: str, **params):
    return client.get("/updates/latest", params={k: v for k, v in params.items() if v})


def serve(body: dict, platform: str = "windows-x86_64", current: str = "1.1.0"):
    return updates.latest(
        path=None,
        inline=json.dumps(body, ensure_ascii=False),
        platform=platform,
        current_version=current,
    )


def test_no_manifest_configured_is_not_an_error(client):
    response = get(client, "", target="windows", arch="x86_64", current_version="1.1.0")
    assert response.status_code == 204
    assert response.content == b""


def test_a_published_newer_version_is_offered_for_this_platform_only(gateway, client, manifest_file):
    gateway.app.state.settings = settings_for(manifest_file(manifest()))

    response = get(client, "", target="windows", arch="x86_64", current_version="1.1.0")

    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == "1.2.0"
    assert payload["channel"] == "stable"
    assert list(payload["platforms"]) == ["windows-x86_64"]
    assert payload["notes"].startswith("Компактный")
    assert payload["platforms"]["windows-x86_64"]["signature"] == SIGNATURE


def test_the_served_body_matches_the_committed_client_contract(manifest_file):
    """One JSON, two languages: this file is committed next to the Rust gate, which parses it with
    the updater's own `RemoteRelease`. A drift fails here instead of on a user's machine."""
    status, body = updates.latest(
        path=manifest_file(manifest()),
        inline=None,
        platform="windows-x86_64",
        current_version="1.1.0",
    )

    assert status == 200
    assert body == json.loads(CONTRACT.read_text(encoding="utf-8"))


def test_an_unsigned_or_private_manifest_answers_503_not_an_update(gateway, client, manifest_file):
    gateway.app.state.settings = settings_for(
        manifest_file(manifest(platforms={"windows-x86_64": {"url": ENTRY["url"]}}))
    )
    refused = get(client, "", target="windows", arch="x86_64", current_version="1.1.0")
    assert refused.status_code == 503
    assert refused.json()["detail"] == "updates_manifest_entry_incomplete:windows-x86_64"

    gateway.app.state.settings = settings_for(manifest_file(manifest(api_key="nope")))
    private = get(client, "", target="windows", arch="x86_64", current_version="1.1.0")
    assert private.status_code == 503
    assert "not_public" in private.json()["detail"]


def test_a_route_level_foreign_architecture_is_never_offered(gateway, client, manifest_file):
    """A published x64 manifest must never become an aarch64 or a Darwin download."""
    gateway.app.state.settings = settings_for(manifest_file(manifest()))

    for target, arch in [
        ("windows", "aarch64"),
        ("linux", "aarch64"),
        ("darwin", "x86_64"),
        ("windows", ""),
    ]:
        response = get(client, "", target=target, arch=arch, current_version="1.1.0")
        assert response.status_code == 204, (target, arch)
        assert response.content == b"", (target, arch)


def test_a_route_level_invalid_manifest_is_refused_not_offered(gateway, client, tmp_path):
    broken = tmp_path / "updates-broken.json"
    broken.write_text("{not json", encoding="utf-8")
    gateway.app.state.settings = settings_for(str(broken))

    response = get(client, "", target="windows", arch="x86_64", current_version="1.1.0")

    assert response.status_code == 503
    assert response.json()["detail"] == "updates_manifest_unreadable"


def test_server_side_downgrade_protection(manifest_file):
    path = manifest_file(manifest("1.2.0"))
    for current, expected in [("1.2.0", 204), ("1.3.0", 204), ("2.0.0", 204), ("1.1.9", 200)]:
        status, body = updates.latest(
            path=path,
            inline=None,
            platform="windows-x86_64",
            current_version=current,
        )
        assert (status, body is None) == (expected, expected == 204), current


def test_unknown_platform_never_receives_another_platform_artifact(manifest_file):
    path = manifest_file(manifest())
    for platform in ["", "darwin-x86_64", "windows-aarch64", None]:
        status, body = updates.latest(
            path=path,
            inline=None,
            platform=platform,
            current_version="1.1.0",
        )
        assert status == 204 and body is None, platform


def test_an_entry_without_a_signature_is_refused(manifest_file):
    path = manifest_file(manifest(platforms={"windows-x86_64": {"url": ENTRY["url"]}}))
    with pytest.raises(updates.UpdateManifestError):
        updates.latest(path=path, inline=None, platform="windows-x86_64", current_version="1.1.0")


def test_a_signature_that_is_not_a_minisign_file_is_refused():
    """What the client would refuse to decode is refused here: a path, a truncated string, a digest."""
    broken = [
        "tests/fixtures/updater/canalla-update-fixture.bin.sig",
        SIGNATURE[:20],
        ARTIFACT_SHA256,
        "dW50cnVzdGVkIGNvbW1lbnQ6IHNpZ25hdHVyZQo=",
    ]
    for signature in broken:
        body = manifest(
            platforms={"windows-x86_64": {**ENTRY, "signature": signature}},
        )
        with pytest.raises(updates.UpdateManifestError) as error:
            serve(body)
        assert "incomplete" in str(error.value)


def test_an_insecure_or_missing_url_is_refused():
    for url in ["http://gateway.12testers.store/x.exe", "downloads/x.exe", "", None]:
        body = manifest(platforms={"windows-x86_64": {**ENTRY, "url": url}})
        with pytest.raises(updates.UpdateManifestError) as error:
            serve(body)
        assert str(error.value).startswith("updates_manifest_entry"), url


def test_a_digest_that_is_not_a_sha256_is_refused():
    body = manifest(platforms={"windows-x86_64": {**ENTRY, "sha256": "deadbeef"}})
    with pytest.raises(updates.UpdateManifestError) as error:
        serve(body)
    assert str(error.value).startswith("updates_manifest_entry_digest")


def test_a_pub_date_the_client_cannot_parse_is_refused():
    for value in ["yesterday", "2026-09-23", "23.09.2026 12:00", 42]:
        with pytest.raises(updates.UpdateManifestError) as error:
            serve(manifest(pub_date=value))
        assert str(error.value) == "updates_manifest_pub_date", value


def test_a_manifest_without_a_pub_date_is_served_without_one():
    """The client parses `pub_date` as RFC 3339: an absent date must be absent, never `""`."""
    for value in [None, ""]:
        status, body = serve(manifest(pub_date=value))
        assert status == 200 and body is not None
        assert "pub_date" not in body, value


def test_a_manifest_that_looks_private_is_never_served(manifest_file):
    path = manifest_file(manifest(api_key="should-never-be-here"))
    with pytest.raises(updates.UpdateManifestError):
        updates.latest(path=path, inline=None, platform="windows-x86_64", current_version="1.1.0")


def test_an_unreadable_manifest_is_an_error_not_an_update(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(updates.UpdateManifestError):
        updates.latest(path=str(path), inline=None, platform="windows-x86_64", current_version="1.1.0")


def test_extra_metadata_survives_and_nothing_else_does(manifest_file):
    path = manifest_file(manifest("1.2.0", minimum_supported_version="1.2.0", mandatory=False))
    status, body = updates.latest(path=path, inline=None, platform="linux-x86_64", current_version="1.1.0")
    assert status == 200 and body is not None
    assert body["minimum_supported_version"] == "1.2.0"
    assert body["mandatory"] is False
    assert "platforms" in body and list(body["platforms"]) == ["linux-x86_64"]
    assert body["platforms"]["linux-x86_64"]["url"] == LINUX_URL


def test_version_comparison_rules():
    assert updates.parse_version("1.2.0") == (1, 2, 0)
    assert updates.parse_version("1.2") is None
    assert updates.parse_version("v1.2.0") is None
    assert updates.is_newer("1.2.0", None) is True
    assert updates.is_newer("1.2.0", "1.1.0") is True
    assert updates.is_newer("1.2.0", "1.2.0") is False
    assert updates.is_newer("1.2.0", "1.10.0") is False
    assert updates.is_newer("garbage", "1.1.0") is False
