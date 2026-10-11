//! The device-session refresh primitive, shared by the desktop app and the native host.
//!
//! `host.rs` is compiled into two binaries — the Tauri app (`main.rs`) and the headless
//! `alex-host-loop` binary — so anything it calls must exist in *both* crate roots. `auth.rs` is
//! only a module of the Tauri app, which is why the refresh lives here instead: it depends on
//! nothing but `credential`, which both binaries include.
//!
//! The rotation is single-flight on purpose. A burst of 401s (a host loop and the webview at once)
//! must not replay a consumed refresh secret against itself: the first caller refreshes and
//! persists the rotated secret, the rest wait and then read the fresh one.

use std::fs;
use std::path::PathBuf;
use std::time::Duration;

use serde_json::{json, Value};

/// The data root. Honours the same override the packaged app uses, without depending on
/// `backend.rs` (a Tauri-only module).
fn data_root() -> PathBuf {
    if let Ok(dir) = std::env::var("ALEX_LLM_DATA_DIR") {
        let path = PathBuf::from(dir);
        if !path.as_os_str().is_empty() {
            return path;
        }
    }
    crate::credential::data_dir()
}

fn runtime_dir() -> PathBuf {
    data_root().join("runtime")
}

fn session_id_path() -> PathBuf {
    runtime_dir().join("session.id")
}

fn install_id_header() -> Option<String> {
    let text = fs::read_to_string(runtime_dir().join("install.id")).ok()?;
    let trimmed = text.trim().to_string();
    (!trimmed.is_empty()).then_some(trimmed)
}

/// The active session id, validated exactly like the app writes it.
pub fn current_session_id() -> Option<String> {
    fs::read_to_string(session_id_path())
        .ok()
        .map(|text| text.trim().to_string())
        .filter(|value| {
            !value.is_empty()
                && value
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.'))
        })
}

fn session_secret(session_id: &str) -> Option<String> {
    crate::credential::load_scoped("session", session_id)
}

/// Drop the local session id and its credential (revoked / expired / unknown).
pub fn clear_local_session() {
    if let Some(id) = current_session_id() {
        let _ = crate::credential::delete_scoped("session", &id);
    }
    let _ = fs::remove_file(session_id_path());
}

fn http_client() -> Result<reqwest::Client, String> {
    reqwest::Client::builder()
        .timeout(Duration::from_secs(15))
        .build()
        .map_err(|error| error.to_string())
}

/// Rotate the persistent session into a fresh one; returns the `/auth/refresh` body.
///
/// Single-flight: concurrent callers perform one network refresh between them.
pub async fn refresh_session(backend_url: &str) -> Result<Value, String> {
    static LOCK: std::sync::OnceLock<tokio::sync::Mutex<()>> = std::sync::OnceLock::new();
    let lock = LOCK.get_or_init(|| tokio::sync::Mutex::new(()));
    let _guard = lock.lock().await;

    let Some(session_id) = current_session_id() else {
        return Err("no_session".into());
    };
    let Some(secret) = session_secret(&session_id) else {
        clear_local_session();
        return Err("no_session".into());
    };
    let client = http_client()?;
    let mut request = client
        .post(format!("{backend_url}/auth/refresh"))
        .json(&json!({"session_id": session_id, "refresh_secret": secret}));
    if let Some(install) = install_id_header() {
        request = request.header("X-Alex-Device-Id", install);
    }
    let response = request
        .send()
        .await
        .map_err(|_| "backend_unreachable".to_string())?;
    if !response.status().is_success() {
        let code = response.status();
        if code == reqwest::StatusCode::UNAUTHORIZED || code == reqwest::StatusCode::CONFLICT {
            // Revoked / expired / unknown session: drop the local credential so the next launch
            // goes to the auth screen instead of retrying.
            clear_local_session();
            return Err("session_rejected".into());
        }
        return Err("backend_unreachable".into());
    }
    let body: Value = response.json().await.map_err(|error| error.to_string())?;
    let fresh_secret = body
        .get("refresh_secret")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or("invalid_refresh_response")?;
    // Rotation: persist the new secret before returning, so a concurrent consumer never replays
    // the consumed one.
    crate::credential::store_scoped("session", &session_id, fresh_secret)?;
    Ok(json!({
        "access_token": body.get("access_token").cloned().unwrap_or(Value::Null),
        "user": body.get("user").cloned().unwrap_or(Value::Null),
        "session_id": session_id,
    }))
}

/// The access token the native host itself uses, or `None` when there is no valid session.
pub async fn refresh_access_token(backend_url: &str) -> Option<String> {
    refresh_session(backend_url)
        .await
        .ok()
        .and_then(|value| {
            value
                .get("access_token")
                .and_then(Value::as_str)
                .map(str::to_string)
        })
        .filter(|token| !token.is_empty())
}
