//! The updater's cryptographic acceptance rule, proven without a network and without a GPU.
//!
//! `tauri-plugin-updater` refuses a package unless its signature verifies against the public key
//! compiled into the app (`tauri.conf.json` → `plugins.updater.pubkey`). This test replays that
//! exact sequence — base64 decode, `PublicKey::decode`, `Signature::decode`, `verify` with
//! `allow_legacy = true` — against a committed fixture that was signed by the updater signing key.
//! The private key stays outside the repository: what is committed here is the artifact, its
//! signature, and the public half in the configuration.
//!
//! Re-sign the fixture (and keep this test honest) with:
//! `npx tauri signer sign -f <private key> -p "" tests/fixtures/updater/canalla-update-fixture.bin`

use base64::engine::general_purpose::STANDARD;
use base64::Engine as _;
use minisign_verify::{PublicKey, Signature};

const FIXTURE: &[u8] = include_bytes!("fixtures/updater/canalla-update-fixture.bin");
const FIXTURE_SIGNATURE: &str = include_str!("fixtures/updater/canalla-update-fixture.bin.sig");
const CONFIG: &str = include_str!("../tauri.conf.json");
/// The body the Gateway serves for one platform. The Gateway suite owns this file and commits it
/// next to the Rust gate, so a shape drift fails here instead of on a user's machine.
const SERVED: &str = include_str!("../../../gateway/tests/fixtures/updates-response-1.2.0.json");

fn configured_pubkey() -> String {
    let config: serde_json::Value =
        serde_json::from_str(CONFIG).expect("tauri.conf.json must be valid JSON");
    config["plugins"]["updater"]["pubkey"]
        .as_str()
        .expect("the updater public key must be configured")
        .to_string()
}

/// The sequence `tauri-plugin-updater::verify_signature` runs before a package may be installed.
fn verify(pubkey_b64: &str, signature_b64: &str, data: &[u8]) -> Result<(), String> {
    let decoded_key = STANDARD
        .decode(pubkey_b64.trim())
        .map_err(|error| error.to_string())?;
    let key_text = String::from_utf8(decoded_key).map_err(|error| error.to_string())?;
    let public_key = PublicKey::decode(&key_text).map_err(|error| error.to_string())?;

    let decoded_signature = STANDARD
        .decode(signature_b64.trim())
        .map_err(|error| error.to_string())?;
    let signature_text =
        String::from_utf8(decoded_signature).map_err(|error| error.to_string())?;
    let signature = Signature::decode(&signature_text).map_err(|error| error.to_string())?;

    public_key
        .verify(data, &signature, true)
        .map_err(|error| error.to_string())
}

/// A second, different key: the same key file with one character of key material changed. The key
/// id still matches, so this proves the signature itself is what is checked.
fn another_key(pubkey_b64: &str) -> String {
    let decoded = STANDARD.decode(pubkey_b64.trim()).expect("the key is base64");
    let text = String::from_utf8(decoded).expect("the key is text");
    let mut lines: Vec<String> = text.lines().map(str::to_string).collect();
    let payload = lines.get(1).cloned().expect("the key file has a payload line");
    let mut bytes = payload.into_bytes();
    let index = bytes.len() / 2;
    bytes[index] = if bytes[index] == b'A' { b'B' } else { b'A' };
    lines[1] = String::from_utf8(bytes).expect("still text");
    STANDARD.encode((lines.join("\n") + "\n").as_bytes())
}

#[test]
fn the_configured_key_parses_and_carries_a_minisign_comment() {
    let key = configured_pubkey();
    let decoded = STANDARD.decode(key.trim()).expect("the configured key is base64");
    let text = String::from_utf8(decoded).expect("the configured key is text");
    assert!(text.starts_with("untrusted comment:"), "{text}");
    assert!(PublicKey::decode(&text).is_ok());
}

#[test]
fn a_package_signed_by_the_updater_key_is_accepted() {
    let result = verify(&configured_pubkey(), FIXTURE_SIGNATURE, FIXTURE);
    assert_eq!(result, Ok(()));
}

#[test]
fn a_tampered_package_is_refused() {
    let mut corrupted = FIXTURE.to_vec();
    let last = corrupted.len() - 1;
    corrupted[last] ^= 0x01;
    let result = verify(&configured_pubkey(), FIXTURE_SIGNATURE, &corrupted);
    assert!(result.is_err(), "a changed byte must not verify");
}

#[test]
fn a_truncated_package_is_refused() {
    let cut = &FIXTURE[..FIXTURE.len() - 1];
    let result = verify(&configured_pubkey(), FIXTURE_SIGNATURE, cut);
    assert!(result.is_err(), "a truncated package must not verify");
}

#[test]
fn a_signature_that_does_not_cover_this_package_is_refused() {
    let result = verify(&configured_pubkey(), FIXTURE_SIGNATURE, b"some other payload");
    assert!(result.is_err(), "the signature is bound to these exact bytes");
}

#[test]
fn a_package_signed_by_another_key_is_refused() {
    let result = verify(&another_key(&configured_pubkey()), FIXTURE_SIGNATURE, FIXTURE);
    assert!(result.is_err(), "another key must not accept this signature");
}

#[test]
fn a_missing_or_garbled_signature_is_refused() {
    let key = configured_pubkey();
    assert!(verify(&key, "", FIXTURE).is_err(), "an empty signature");
    assert!(
        verify(&key, "not base64 at all !!", FIXTURE).is_err(),
        "a signature that is not base64"
    );
    let junk = STANDARD.encode(b"this is not a minisign signature file");
    assert!(
        verify(&key, &junk, FIXTURE).is_err(),
        "base64 that carries no signature"
    );
}

#[test]
fn a_garbled_public_key_is_refused_instead_of_being_ignored() {
    assert!(verify("not base64", FIXTURE_SIGNATURE, FIXTURE).is_err());
    let empty_key = STANDARD.encode(b"");
    assert!(verify(&empty_key, FIXTURE_SIGNATURE, FIXTURE).is_err());
}

// ---------------------------------------------------------------- the served body, as the client reads it

fn served() -> tauri_plugin_updater::RemoteRelease {
    serde_json::from_str(SERVED).expect("the served body must parse the way the updater parses it")
}

#[test]
fn the_gateway_response_is_readable_by_the_updater() {
    let release = served();
    assert_eq!(release.version.to_string(), "1.2.0");
    assert!(release.notes.is_some(), "the release notes survive");
    assert!(release.pub_date.is_some(), "the served date is RFC 3339");
    let url = release
        .download_url("windows-x86_64")
        .expect("a windows artifact is offered");
    assert!(url.as_str().starts_with("https://"), "{url}");
}

#[test]
fn the_signature_served_for_the_release_verifies_against_the_package() {
    let release = served();
    let signature = release
        .signature("windows-x86_64")
        .expect("the served signature is readable");
    assert_eq!(verify(&configured_pubkey(), signature, FIXTURE), Ok(()));
}

#[test]
fn a_platform_the_release_does_not_describe_has_no_download() {
    let release = served();
    assert!(release.download_url("darwin-aarch64").is_err());
    assert!(
        release.download_url("linux-x86_64").is_err(),
        "the endpoint answers one platform at a time"
    );
}

#[test]
fn an_empty_pub_date_would_not_parse_which_is_why_the_endpoint_omits_it() {
    let broken = SERVED.replace("\"pub_date\": \"2026-09-23T00:00:00Z\"", "\"pub_date\": \"\"");
    assert_ne!(broken, SERVED, "the fixture still carries the date this test replaces");
    assert!(
        serde_json::from_str::<tauri_plugin_updater::RemoteRelease>(&broken).is_err(),
        "the client refuses an unparsable date, so the endpoint must omit it"
    );
}
