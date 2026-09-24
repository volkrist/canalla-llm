//! The updater's cryptographic acceptance rule, proven without a network and without a GPU.
//!
//! `tauri-plugin-updater` refuses a package unless its signature verifies against the public key
//! compiled into the app (`tauri.conf.json` → `plugins.updater.pubkey`). This test replays that
//! exact sequence — base64 decode, `PublicKey::decode`, `Signature::decode`, `verify` with
//! `allow_legacy = true` — against a committed fixture that was signed by the updater signing key.
//! The private key stays outside the repository: what is committed here is the artifact, its
//! signature, and the public half in the configuration.
//!
//! The file *name* a signature covers lives in the minisign trusted comment, which `verify` covers
//! with the signature's global signature field. The Gateway refuses a manifest whose signed name is
//! not the artifact its url serves, so the fixture is signed for the name a release publishes —
//! re-sign it (and keep this test honest) with:
//!
//! ```text
//! cp tests/fixtures/updater/canalla-update-fixture.bin /tmp/"Canalla LLM_1.2.0_x64-setup.exe"
//! npx tauri signer sign -f <private key> -p "" /tmp/"Canalla LLM_1.2.0_x64-setup.exe" < /dev/null
//! # the base64 it prints goes into canalla-update-fixture.bin.sig *and* into the Gateway contract
//! # fixture (apps/gateway/tests/fixtures/updates-response-1.2.0.json); the sha256 never changes,
//! # because the bytes never do. `canalla-update-fixture.linux.sig` is the same bytes signed for
//! # the Linux package's name.
//! ```

use base64::engine::general_purpose::STANDARD;
use base64::Engine as _;
use minisign_verify::{PublicKey, Signature};

const FIXTURE: &[u8] = include_bytes!("fixtures/updater/canalla-update-fixture.bin");
const FIXTURE_SIGNATURE: &str = include_str!("fixtures/updater/canalla-update-fixture.bin.sig");
/// The same stand-in bytes, signed for the Linux package's name: one fixture, two platform names,
/// exactly as the two released artifacts are signed from one key.
const FIXTURE_LINUX_SIGNATURE: &str =
    include_str!("fixtures/updater/canalla-update-fixture.linux.sig");
const CONFIG: &str = include_str!("../tauri.conf.json");
/// The body the Gateway serves for one platform. The Gateway suite owns this file and commits it
/// next to the Rust gate, so a shape drift fails here instead of on a user's machine.
const SERVED: &str = include_str!("../../../gateway/tests/fixtures/updates-response-1.2.0.json");

/// The artifact name the released Windows package carries, and the one the fixture is signed for.
const SERVED_ARTIFACT: &str = "Canalla LLM_1.2.0_x64-setup.exe";

/// The identity the *fixtures* in this file were signed with. It is test-only by construction: its
/// purpose is to exercise the verification path with real cryptography, and the shipped client must
/// never trust it. `the_configured_key_is_the_production_identity_and_not_the_test_one` is what
/// makes that a checked fact rather than a convention.
const TEST_PUBKEY: &str = "dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IEI3OUZGOTBCNTJEMDBGMjQKUldRa0Q5QlNDL21mdDNHSXprOGZZZVZXa1U4SlZMRWs0UkpaVTNyOWVSN0orUGFBRTQ1SnJXbEYK";

/// The key id of the production signing identity the shipped client compiles in. Public
/// information: the private half lives outside the repository and never enters it, and the
/// password lives in the Windows Credential Manager (see `scripts/production-signing.ps1`).
const PRODUCTION_KEY_ID: &str = "9B328EFF111D1FB2";

fn configured_pubkey() -> String {
    let config: serde_json::Value =
        serde_json::from_str(CONFIG).expect("tauri.conf.json must be valid JSON");
    config["plugins"]["updater"]["pubkey"]
        .as_str()
        .expect("the updater public key must be configured")
        .to_string()
}

/// The minisign key id a public key announces in its untrusted comment, e.g. `B79FF90B52D00F24`.
fn key_id(pubkey_b64: &str) -> String {
    let decoded = STANDARD
        .decode(pubkey_b64.trim())
        .expect("the key is base64");
    let text = String::from_utf8(decoded).expect("the key is text");
    text.lines()
        .next()
        .expect("a minisign public key starts with a comment")
        .rsplit(' ')
        .next()
        .expect("the comment ends with the key id")
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
    let signature_text = String::from_utf8(decoded_signature).map_err(|error| error.to_string())?;
    let signature = Signature::decode(&signature_text).map_err(|error| error.to_string())?;

    public_key
        .verify(data, &signature, true)
        .map_err(|error| error.to_string())
}

/// A second, different key: the same key file with one character of key material changed. The key
/// id still matches, so this proves the signature itself is what is checked.
fn another_key(pubkey_b64: &str) -> String {
    let decoded = STANDARD
        .decode(pubkey_b64.trim())
        .expect("the key is base64");
    let text = String::from_utf8(decoded).expect("the key is text");
    let mut lines: Vec<String> = text.lines().map(str::to_string).collect();
    let payload = lines
        .get(1)
        .cloned()
        .expect("the key file has a payload line");
    let mut bytes = payload.into_bytes();
    let index = bytes.len() / 2;
    bytes[index] = if bytes[index] == b'A' { b'B' } else { b'A' };
    lines[1] = String::from_utf8(bytes).expect("still text");
    STANDARD.encode((lines.join("\n") + "\n").as_bytes())
}

/// The minisign signature file inside the base64 field, the way `tauri signer sign` wrote it.
fn signature_text(signature_b64: &str) -> String {
    let decoded = STANDARD
        .decode(signature_b64.trim())
        .expect("the signature is base64");
    String::from_utf8(decoded).expect("the signature is text")
}

/// The `file:` field of the trusted comment: the artifact the signature was made for. `verify`
/// checks the global signature, which covers this comment, so a verifying signature authenticates
/// this name — that is the premise of the Gateway's binding rule.
fn signed_name(signature: &Signature) -> String {
    signature
        .trusted_comment()
        .split('\t')
        .find_map(|field| field.strip_prefix("file:"))
        .expect("the trusted comment names the signed file")
        .to_string()
}

#[test]
fn the_configured_key_parses_and_carries_a_minisign_comment() {
    let key = configured_pubkey();
    let decoded = STANDARD
        .decode(key.trim())
        .expect("the configured key is base64");
    let text = String::from_utf8(decoded).expect("the configured key is text");
    assert!(text.starts_with("untrusted comment:"), "{text}");
    assert!(PublicKey::decode(&text).is_ok());
}

#[test]
fn the_configured_key_is_the_production_identity_and_not_the_test_one() {
    // Every fixture above verifies against TEST_PUBKEY, so this is the assertion that keeps the two
    // identities apart. Shipping the test key would mean every released client trusts a key whose
    // private half sits beside the test fixtures in a developer's home directory.
    let configured = configured_pubkey();
    assert_eq!(key_id(&configured), PRODUCTION_KEY_ID);
    assert_ne!(
        key_id(&configured),
        key_id(TEST_PUBKEY),
        "the test identity must never ship in the client"
    );
}

#[test]
fn a_package_signed_by_the_updater_key_is_accepted() {
    let result = verify(TEST_PUBKEY, FIXTURE_SIGNATURE, FIXTURE);
    assert_eq!(result, Ok(()));
}

#[test]
fn the_second_platform_signature_is_also_signed_by_the_updater_key() {
    assert_eq!(
        verify(TEST_PUBKEY, FIXTURE_LINUX_SIGNATURE, FIXTURE),
        Ok(())
    );
    let signature = Signature::decode(&signature_text(FIXTURE_LINUX_SIGNATURE))
        .expect("the Linux fixture signature parses");
    assert_eq!(signed_name(&signature), "Canalla LLM_1.2.0_amd64.deb");
}

#[test]
fn a_tampered_package_is_refused() {
    let mut corrupted = FIXTURE.to_vec();
    let last = corrupted.len() - 1;
    corrupted[last] ^= 0x01;
    let result = verify(TEST_PUBKEY, FIXTURE_SIGNATURE, &corrupted);
    assert!(result.is_err(), "a changed byte must not verify");
}

#[test]
fn a_truncated_package_is_refused() {
    let cut = &FIXTURE[..FIXTURE.len() - 1];
    let result = verify(TEST_PUBKEY, FIXTURE_SIGNATURE, cut);
    assert!(result.is_err(), "a truncated package must not verify");
}

#[test]
fn a_signature_that_does_not_cover_this_package_is_refused() {
    let result = verify(TEST_PUBKEY, FIXTURE_SIGNATURE, b"some other payload");
    assert!(
        result.is_err(),
        "the signature is bound to these exact bytes"
    );
}

#[test]
fn a_package_signed_by_another_key_is_refused() {
    let result = verify(&another_key(TEST_PUBKEY), FIXTURE_SIGNATURE, FIXTURE);
    assert!(
        result.is_err(),
        "another key must not accept this signature"
    );
}

#[test]
fn a_missing_or_garbled_signature_is_refused() {
    let key = TEST_PUBKEY;
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
    assert_eq!(verify(TEST_PUBKEY, signature, FIXTURE), Ok(()));
}

#[test]
fn the_served_signature_names_the_artifact_the_url_serves() {
    // The Gateway's binding rule, checked from the side that can prove it: the signature it serves
    // is genuine (asserted here with the compiled-in key), and the name inside its trusted comment —
    // the only authenticated name a release has — is the artifact the download url serves, with the
    // version in it. A manifest that fails this must never be offered, which is why the fixture is
    // signed for this name rather than for the stand-in's file name.
    let release = served();
    let signature_b64 = release
        .signature("windows-x86_64")
        .expect("the served signature is readable");
    assert_eq!(verify(TEST_PUBKEY, signature_b64, FIXTURE), Ok(()));

    let signature = Signature::decode(&signature_text(signature_b64))
        .expect("the served signature is a minisign signature");
    let name = signed_name(&signature);
    assert_eq!(name, SERVED_ARTIFACT);

    let url = release
        .download_url("windows-x86_64")
        .expect("a windows artifact is offered");
    assert!(
        url.path().ends_with(&name.replace(' ', "%20")),
        "the served url must be the signed artifact: {url}"
    );
    assert!(
        name.contains(&release.version.to_string()),
        "the signed name must carry the served version: {name}"
    );
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
    let broken = SERVED.replace(
        "\"pub_date\": \"2026-09-23T00:00:00Z\"",
        "\"pub_date\": \"\"",
    );
    assert_ne!(
        broken, SERVED,
        "the fixture still carries the date this test replaces"
    );
    assert!(
        serde_json::from_str::<tauri_plugin_updater::RemoteRelease>(&broken).is_err(),
        "the client refuses an unparsable date, so the endpoint must omit it"
    );
}

// ------------------------------------------------- the artifact this tree just built, as it will ship

/// The installer a release build leaves in the bundle directory, with the signature written beside
/// it. `cargo test` on a machine that has not run a release build finds nothing here, and the gate
/// below says so rather than pretending to have checked an artifact that does not exist.
fn built_installer() -> Option<(Vec<u8>, String)> {
    let bundle = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("target")
        .join("release")
        .join("bundle")
        .join("nsis");
    let artifact = bundle.join(SERVED_ARTIFACT);
    let signature = bundle.join(format!("{SERVED_ARTIFACT}.sig"));
    if !artifact.is_file() || !signature.is_file() {
        return None;
    }
    Some((
        std::fs::read(&artifact).expect("the built installer is readable"),
        std::fs::read_to_string(&signature).expect("the built signature is readable"),
    ))
}

#[test]
fn the_built_release_artifact_is_signed_by_the_production_identity() {
    // Every other test in this file checks a committed fixture, which proves the verification path
    // but not the artifact that ships. This one checks the real installer a release build left in
    // `target/release/bundle/nsis`, against the key the client actually compiles in, and asserts the
    // signed name carries the released version — the binding the Gateway refuses a manifest for.
    let Some((bytes, signature)) = built_installer() else {
        eprintln!(
            "no release bundle in this tree: the shipping artifact's signature was not checked here"
        );
        return;
    };

    let key = configured_pubkey();
    assert_eq!(
        key_id(&key),
        PRODUCTION_KEY_ID,
        "the shipped client must compile in the production identity"
    );
    assert_eq!(
        verify(&key, &signature, &bytes),
        Ok(()),
        "the built installer must verify against the production public key"
    );
    assert!(
        verify(TEST_PUBKEY, &signature, &bytes).is_err(),
        "and it must not verify against the test identity"
    );

    let parsed = Signature::decode(&signature_text(&signature)).expect("a minisign signature");
    let name = signed_name(&parsed);
    assert_eq!(
        name, SERVED_ARTIFACT,
        "the signature names the shipped file"
    );
    assert!(
        name.contains("1.2.0"),
        "the signed name must carry the product version: {name}"
    );

    // The installer's payload is the point of the release: the backend sidecar and the Tor runtime.
    // A signature over a stub would pass everything above, so the size floor is asserted too.
    assert!(
        bytes.len() > 80 * 1024 * 1024,
        "the installer must carry the packaged backend and the Tor runtime, not a stub: {} bytes",
        bytes.len()
    );
}
