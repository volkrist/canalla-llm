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

/// How many times one refresh may be attempted before the local session is given up.
///
/// Two consumers share one persisted credential: the desktop webview and the native host
/// loop. Each reads the secret immediately before sending, but a refresh *rotates* it, so
/// when both start inside the same few milliseconds the loser presents the secret the
/// winner has just consumed. The Gateway answers that replay with 401/409. Treating the
/// first rejection as final deleted `runtime/session.id` and the credential, silently
/// logging the owner out (observed after the 1.2.3 reinstall). A short retry re-reads the
/// store, sees the winner's rotated secret and succeeds; only a rejection that survives
/// every retry is a real revocation.
const REFRESH_ATTEMPTS: usize = 3;

fn retry_delay(attempt: usize) -> Duration {
    Duration::from_millis(200 * (attempt as u64 + 1))
}

/// Rotate the persistent session into a fresh one; returns the `/auth/refresh` body.
///
/// Single-flight: concurrent callers inside this process perform one network refresh
/// between them, and a rejection caused by a *different* process's rotation is retried with
/// the freshly stored secret instead of clearing the session.
pub async fn refresh_session(backend_url: &str) -> Result<Value, String> {
    static LOCK: std::sync::OnceLock<tokio::sync::Mutex<()>> = std::sync::OnceLock::new();
    let lock = LOCK.get_or_init(|| tokio::sync::Mutex::new(()));
    let _guard = lock.lock().await;

    let Some(initial_session_id) = current_session_id() else {
        return Err("no_session".into());
    };
    let mut rejected = false;
    for attempt in 0..REFRESH_ATTEMPTS {
        if attempt > 0 {
            tokio::time::sleep(retry_delay(attempt - 1)).await;
        }
        // Re-read both the id and the secret: the other consumer may have rotated or
        // replaced them while we were waiting.
        let session_id = current_session_id().unwrap_or_else(|| initial_session_id.clone());
        let Some(secret) = session_secret(&session_id) else {
            // The id and the credential are written by different processes; a clear that
            // races a successful rotation is not a reason to erase the session by hand.
            if attempt + 1 < REFRESH_ATTEMPTS {
                continue;
            }
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
                rejected = true;
                continue;
            }
            return Err("backend_unreachable".into());
        }
        let body: Value = response.json().await.map_err(|error| error.to_string())?;
        let fresh_secret = body
            .get("refresh_secret")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .ok_or("invalid_refresh_response")?;
        // Rotation: persist the new secret before returning, so a concurrent consumer never
        // replays the consumed one.
        crate::credential::store_scoped("session", &session_id, fresh_secret)?;
        return Ok(json!({
            "access_token": body.get("access_token").cloned().unwrap_or(Value::Null),
            "user": body.get("user").cloned().unwrap_or(Value::Null),
            "session_id": session_id,
        }));
    }
    if rejected {
        // Revoked / expired / unknown even after every retry: drop the local credential so
        // the next launch goes to the auth screen instead of retrying.
        clear_local_session();
        return Err("session_rejected".into());
    }
    Err("no_session".into())
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    use std::net::TcpListener;

    fn unique() -> String {
        format!(
            "{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        )
    }

    /// A one-shot HTTP/1.1 server. `answers` are statuses served in order; `between` runs just
    /// before each answer is written, which is how the test plays "another process rotated the
    /// credential while this request was in flight".
    fn serve<F>(
        answers: Vec<(u16, String)>,
        mut between: F,
    ) -> (String, std::thread::JoinHandle<()>)
    where
        F: FnMut(usize) + Send + 'static,
    {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let handle = std::thread::spawn(move || {
            for (index, (status, body)) in answers.into_iter().enumerate() {
                let (mut stream, _) = listener.accept().unwrap();
                let mut buffer = [0u8; 8192];
                let _ = stream.read(&mut buffer);
                between(index);
                let head = format!("HTTP/1.1 {status} X\r\ncontent-type: application/json\r\n");
                let response = format!(
                    "{head}content-length: {}\r\nconnection: close\r\n\r\n{body}",
                    body.len()
                );
                let _ = stream.write_all(response.as_bytes());
                let _ = stream.flush();
            }
        });
        (format!("http://{address}"), handle)
    }

    struct Isolated {
        dir: PathBuf,
        previous_root: Option<String>,
    }

    impl Isolated {
        fn new() -> Self {
            let previous_root = std::env::var("ALEX_LLM_DATA_DIR").ok();
            let dir = std::env::temp_dir().join(format!("alex-session-test-{}", unique()));
            let _ = fs::create_dir_all(dir.join("runtime"));
            std::env::set_var("ALEX_LLM_DATA_DIR", &dir);
            Self { dir, previous_root }
        }
    }

    impl Drop for Isolated {
        fn drop(&mut self) {
            match &self.previous_root {
                Some(value) => std::env::set_var("ALEX_LLM_DATA_DIR", value),
                None => std::env::remove_var("ALEX_LLM_DATA_DIR"),
            }
            let _ = fs::remove_dir_all(&self.dir);
        }
    }

    /// The 25 Oct 1.2.3 defect: the desktop and the host loop share one rotated secret. The
    /// loser's replay was treated as a revocation, so `runtime/session.id` and the credential
    /// were deleted and the owner was silently logged out. A retry must pick up the winner's
    /// rotated secret instead.
    #[test]
    fn a_racing_rotation_is_retried_not_treated_as_a_logout() {
        let _guard = crate::credential::TEST_ENV_LOCK
            .lock()
            .unwrap_or_else(|error| error.into_inner());
        let isolated = Isolated::new();
        let session_id = format!("unit-{}", unique());
        fs::write(isolated.dir.join("runtime").join("session.id"), &session_id).unwrap();
        if crate::credential::store_scoped("session", &session_id, "consumed-secret").is_err() {
            // No usable secure store on this machine: the contract's answer is the typed
            // refusal, which is covered by the credential tests.
            return;
        }
        let rotated = session_id.clone();
        let (url, server) = serve(
            vec![
                (401, "{}".to_string()),
                (
                    200,
                    json!({
                        "access_token": "fresh-token",
                        "refresh_secret": "rotated-secret",
                        "session_id": session_id.clone(),
                    })
                    .to_string(),
                ),
            ],
            move |index| {
                if index == 0 {
                    // The other process won the race and persisted its rotated secret while
                    // this request was in flight.
                    let _ = crate::credential::store_scoped("session", &rotated, "winner-secret");
                }
            },
        );
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap();
        let result = runtime.block_on(refresh_session(&url));
        server.join().unwrap();

        let body =
            result.expect("a rejected refresh caused by a rotation must not log the owner out");
        assert_eq!(body["access_token"], Value::String("fresh-token".into()));
        assert!(
            isolated.dir.join("runtime").join("session.id").is_file(),
            "session.id must survive a rotation race"
        );
        assert_eq!(
            crate::credential::load_scoped("session", &session_id).as_deref(),
            Some("rotated-secret")
        );
        let _ = crate::credential::delete_scoped("session", &session_id);
    }

    /// The other half of the contract: a session the Gateway rejects on every attempt is still
    /// cleared, so the next launch shows the auth screen instead of retrying forever.
    #[test]
    fn a_session_rejected_on_every_attempt_is_cleared() {
        let _guard = crate::credential::TEST_ENV_LOCK
            .lock()
            .unwrap_or_else(|error| error.into_inner());
        let isolated = Isolated::new();
        let session_id = format!("unit-{}", unique());
        fs::write(isolated.dir.join("runtime").join("session.id"), &session_id).unwrap();
        if crate::credential::store_scoped("session", &session_id, "revoked-secret").is_err() {
            return;
        }
        let answers = (0..REFRESH_ATTEMPTS)
            .map(|_| (401, "{}".to_string()))
            .collect();
        let (url, server) = serve(answers, |_| {});
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap();
        let result = runtime.block_on(refresh_session(&url));
        server.join().unwrap();

        assert_eq!(result, Err("session_rejected".into()));
        assert!(
            !isolated.dir.join("runtime").join("session.id").is_file(),
            "a genuinely revoked session is cleared"
        );
        assert!(crate::credential::load_scoped("session", &session_id).is_none());
    }
}
