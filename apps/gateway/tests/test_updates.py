"""The update endpoint: public metadata only, no downgrades, no unbound artifacts.

Every case here is offline: the manifest is a temporary file (or inline JSON), and the endpoint is
the only thing under test. No provider traffic, no compute, no client.

The signature and the digest below belong to the committed updater fixture
(`apps/desktop/src-tauri/tests/fixtures/updater/`), so what these tests serve is exactly what a real
release publishes: the signature was produced by `npx tauri signer sign` for a copy of the stand-in
named like the released installer, and the same bytes are parsed by the Rust gate with the updater's
own `RemoteRelease` type - which also verifies that signature against the compiled-in public key.
There is therefore no hand-made signature in this module: the accepted cases carry the committed
signature, and every rejected case is the committed signature (or the committed manifest) with its
*metadata* changed.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import unquote

import pytest
from pydantic import SecretStr

from gateway import updates
from gateway.config import GatewaySettings

REPO = Path(__file__).resolve().parents[3]
UPDATER_FIXTURES = REPO / "apps" / "desktop" / "src-tauri" / "tests" / "fixtures" / "updater"
SIGNATURE = (UPDATER_FIXTURES / "canalla-update-fixture.bin.sig").read_text(encoding="utf-8").strip()
LINUX_SIGNATURE = (UPDATER_FIXTURES / "canalla-update-fixture.linux.sig").read_text(encoding="utf-8").strip()
ARTIFACT_SHA256 = hashlib.sha256((UPDATER_FIXTURES / "canalla-update-fixture.bin").read_bytes()).hexdigest()
CONTRACT = Path(__file__).parent / "fixtures" / "updates-response-1.2.0.json"

WINDOWS_URL = "https://gateway.12testers.store/downloads/Canalla%20LLM_1.2.0_x64-setup.exe"
LINUX_URL = "https://gateway.12testers.store/downloads/Canalla%20LLM_1.2.0_amd64.deb"
OLD_WINDOWS_URL = "https://gateway.12testers.store/downloads/Canalla%20LLM_1.1.0_x64-setup.exe"
NEXT_WINDOWS_URL = "https://gateway.12testers.store/downloads/Canalla%20LLM_1.2.1_x64-setup.exe"
NOTES = "Компактный интерфейс, always-ready сервисы, автообновление."

ENTRY = {"url": WINDOWS_URL, "signature": SIGNATURE, "sha256": ARTIFACT_SHA256}
LINUX_ENTRY = {"url": LINUX_URL, "signature": LINUX_SIGNATURE, "sha256": ARTIFACT_SHA256}


def served_name(url: str) -> str:
    """The file the url serves the way the endpoint reads it: the decoded basename."""
    return unquote(url.rsplit("/", 1)[-1])


def signed_name_of(signature: str) -> str:
    """The `file:` field of the signature's trusted comment, read from the committed base64.

    This is deliberately independent of `gateway.updates`: it decodes the fixture the way the client
    and the Rust gate do, so a drift between the two readings cannot pass unnoticed.
    """
    text = base64.b64decode(signature).decode("utf-8")
    comment = next(line for line in text.splitlines() if line.startswith("trusted comment:"))
    return next(field[len("file:") :] for field in comment.split("\t") if field.startswith("file:"))


def manifest(version: str = "1.2.0", *, platforms=None, **extra) -> dict:
    body = {
        "version": version,
        "channel": "stable",
        "pub_date": "2026-09-23T00:00:00Z",
        "notes": NOTES,
        "platforms": platforms
        if platforms is not None
        else {
            # Both entries carry the committed signature that names their own artifact file.
            "windows-x86_64": dict(ENTRY),
            "linux-x86_64": dict(LINUX_ENTRY),
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
    body = manifest()
    gateway.app.state.settings = settings_for(manifest_file(body))

    response = get(client, "", target="windows", arch="x86_64", current_version="1.1.0")

    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == "1.2.0"
    assert payload["channel"] == "stable"
    assert list(payload["platforms"]) == ["windows-x86_64"]
    assert payload["notes"].startswith("Компактный")
    # The whole entry, signature included: what goes out is what the manifest published.
    assert payload["platforms"]["windows-x86_64"] == ENTRY


def test_the_served_body_matches_the_committed_client_contract(manifest_file):
    """One JSON, two languages: this file is committed next to the Rust gate, which parses it with
    the updater's own `RemoteRelease` - and verifies its signature against the compiled-in key. The
    served body has to equal it field for field, so a drift fails here instead of on a user's
    machine."""
    status, body = updates.latest(
        path=manifest_file(manifest()),
        inline=None,
        platform="windows-x86_64",
        current_version="1.1.0",
    )

    assert status == 200 and body is not None
    assert body == json.loads(CONTRACT.read_text(encoding="utf-8"))


def test_the_committed_fixture_is_bound_and_served(manifest_file):
    """The fixture that `updater_signature.rs` verifies is served, and it binds.

    It is the real cryptography of this suite: `npx tauri signer sign` signed the stand-in bytes for
    a copy named like the released artifact, so the trusted comment names that file and carries the
    version the manifest advertises. This test reads the committed base64 from scratch (it does not
    go through `gateway.updates`) and compares it with the file each url serves.
    """
    status, body = updates.latest(
        path=manifest_file(manifest()),
        inline=None,
        platform="windows-x86_64",
        current_version="1.1.0",
    )

    assert status == 200 and body is not None
    assert body["platforms"]["windows-x86_64"]["signature"] == SIGNATURE
    for signature, url in [(SIGNATURE, WINDOWS_URL), (LINUX_SIGNATURE, LINUX_URL)]:
        assert signed_name_of(signature) == served_name(url)
    assert signed_name_of(SIGNATURE) == "Canalla LLM_1.2.0_x64-setup.exe"
    assert signed_name_of(LINUX_SIGNATURE) == "Canalla LLM_1.2.0_amd64.deb"


def test_a_route_level_binding_failure_is_a_typed_error_not_an_update(gateway, client, manifest_file):
    """How the refusal reaches a client: the same typed answer the other broken manifests get.

    The material is the committed signature offered under an older artifact's url - the published
    1.1.0 file advertised as the 1.2.0 release.
    """
    gateway.app.state.settings = settings_for(
        manifest_file(manifest(platforms={"windows-x86_64": {**ENTRY, "url": OLD_WINDOWS_URL}}))
    )

    response = get(client, "", target="windows", arch="x86_64", current_version="1.1.0")

    assert response.status_code == 503
    assert response.json()["detail"] == "updates_manifest_entry_unbound:windows-x86_64"


def test_a_bound_manifest_is_still_served_for_both_platforms(gateway, client, manifest_file):
    """The control for the binding rule: it refuses unbound metadata, it does not disable updates."""
    gateway.app.state.settings = settings_for(manifest_file(manifest()))

    for target, arch, entry in [
        ("windows", "x86_64", ENTRY),
        ("linux", "x86_64", LINUX_ENTRY),
    ]:
        response = get(client, "", target=target, arch=arch, current_version="1.1.0")
        assert response.status_code == 200, (target, arch)
        served = response.json()["platforms"]
        assert len(served) == 1 and list(served) == [f"{target}-{arch}"]
        assert served[f"{target}-{arch}"] == entry


def test_a_signed_name_that_is_not_the_served_file_is_refused(manifest_file):
    """The attack: a genuine signature's name no longer matches the artifact the url serves.

    The Windows package's signature is offered as the Linux package: the url is a real release url,
    the signature is genuine, and they are simply not the same artifact.
    """
    path = manifest_file(manifest(platforms={"windows-x86_64": {**ENTRY, "url": LINUX_URL}}))
    with pytest.raises(updates.UpdateManifestError) as error:
        updates.latest(path=path, inline=None, platform="windows-x86_64", current_version="1.1.0")
    assert str(error.value) == "updates_manifest_entry_unbound:windows-x86_64"


def test_an_old_validly_signed_artifact_cannot_be_replayed_as_a_newer_release():
    """The attack this rule exists for: the genuine 1.2.0 package is republished as 1.2.1.

    The signature is real, the bytes it covers are the ones already released, and only the file name
    the url advertises (and the version in the manifest) say 1.2.1 - so the binding refuses it.
    """
    body = manifest("1.2.1", platforms={"windows-x86_64": {**ENTRY, "url": NEXT_WINDOWS_URL}})
    with pytest.raises(updates.UpdateManifestError) as error:
        serve(body)
    assert str(error.value) == "updates_manifest_entry_unbound:windows-x86_64"


def test_a_manifest_version_that_is_not_the_version_in_the_signed_name_is_refused():
    """The same rule seen from the other side: the signed name says 1.2.0, the manifest says 1.2.1,
    and the artifact the url serves is the unchanged 1.2.0 file."""
    body = manifest("1.2.1", platforms={"windows-x86_64": dict(ENTRY)})
    with pytest.raises(updates.UpdateManifestError) as error:
        serve(body)
    assert str(error.value) == "updates_manifest_entry_unbound:windows-x86_64"


def test_a_pre_release_name_is_never_a_release_version():
    """`Canalla LLM_1.2.0-beta.1_x64-setup.exe` is a version, and it is not the 1.2.0 release.

    The extraction keeps the pre-release suffix with its token, so the name cannot stand in for the
    release it precedes - and a manifest for a pre-release is refused by the version rule as well.
    """
    assert updates.VERSION_IN_NAME.findall("Canalla LLM_1.2.0-beta.1_x64-setup.exe") == ["1.2.0-beta.1"]
    assert updates.VERSION_IN_NAME.findall("Canalla LLM_1.2.0_x64-setup.exe") == ["1.2.0"]
    assert updates.parse_version("1.2.0-beta.1") is None
    assert updates.is_newer("1.2.0-beta.1", "1.1.0") is False


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
