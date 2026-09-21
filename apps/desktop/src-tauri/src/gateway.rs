//! Alex Cloud (Central RunPod Gateway) enrollment for the desktop client.
//!
//! The installation credential is the client's cloud identity. It lives in the same
//! secure per-installation store as the device credential
//! (`Alex LLM/gateway/installation`), never in the frontend, never in the local
//! database, and it survives logout, restart and reinstall. The RunPod master key is
//! **server-side only**: this module can neither read nor store it.
//!
//! Local users are not cloud identities, so nothing here depends on the signed-in user.

use serde_json::{json, Value};
use std::time::Duration;

use crate::credential;

pub const GATEWAY_PROTOCOL_VERSION: u64 = 1;
const HTTP_TIMEOUT: Duration = Duration::from_secs(20);
const HEALTH_TIMEOUT: Duration = Duration::from_secs(4);
const MAX_ACTIVATION_CODE: usize = 200;

/// Build/runtime default. Packaged installers ship a real Gateway URL; a developer
/// checkout leaves it empty so the field stays editable in Settings.
pub fn default_gateway_url() -> String {
    std::env::var("ALEX_GATEWAY_URL")
        .unwrap_or_default()
        .trim()
        .trim_end_matches('/')
        .to_string()
}

/// Parsed and validated Gateway URL. Plaintext is allowed only through a loopback
/// tunnel: the installation secret is a bearer credential (fail closed).
pub fn validate_url(raw: &str) -> Result<String, String> {
    let value = raw.trim().trim_end_matches('/');
    if value.is_empty() || value.len() > 300 {
        return Err("invalid_url".into());
    }
    let rest = match value.split_once("://") {
        Some(("https", rest)) => rest,
        Some(("http", rest)) => {
            let host = host_of(rest);
            if !matches!(host.as_str(), "localhost" | "127.0.0.1" | "::1" | "[::1]") {
                return Err("insecure_gateway_url".into());
            }
            rest
        }
        _ => return Err("invalid_url".into()),
    };
    let authority = rest.split('/').next().unwrap_or("").split('@').last().unwrap_or("");
    if authority.is_empty() {
        return Err("invalid_url".into());
    }
    if rest.contains('@') || rest.contains('?') || rest.contains('#') {
        return Err("invalid_url".into());
    }
    Ok(value.to_string())
}

fn host_of(rest: &str) -> String {
    let authority = rest.split('/').next().unwrap_or("");
    let host = authority.rsplit('@').next().unwrap_or(authority);
    match host.rfind(':') {
        Some(index) if !host.contains(']') || host.starts_with('[') => {
            let (name, port) = host.split_at(index);
            if port[1..].chars().all(|c| c.is_ascii_digit()) {
                return name.to_string();
            }
            host.to_string()
        }
        _ => host.to_string(),
    }
}

/// Credential name for the installation enrollment. `ALEX_GATEWAY_CREDENTIAL_NAME` is a
/// test/development override so the suite never touches the real installation entry.
fn credential_name() -> String {
    match std::env::var("ALEX_GATEWAY_CREDENTIAL_NAME") {
        Ok(value)
            if !value.trim().is_empty()
                && value.len() <= 80
                && value
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.')) =>
        {
            value
        }
        _ => "installation".to_string(),
    }
}

/// The stored enrollment: URL + public installation id + installation secret.
fn load_enrollment() -> Option<Value> {
    let raw = credential::load_scoped("gateway", &credential_name())?;
    let value: Value = serde_json::from_str(&raw).ok()?;
    if value.get("url").and_then(Value::as_str).is_some()
        && value.get("installation_id").and_then(Value::as_str).is_some()
        && value.get("installation_secret").and_then(Value::as_str).is_some()
    {
        Some(value)
    } else {
        None
    }
}

fn client() -> Result<reqwest::blocking::Client, String> {
    reqwest::blocking::Client::builder()
        .timeout(HTTP_TIMEOUT)
        .build()
        .map_err(|_| "gateway_unreachable".to_string())
}

fn text_of(response: reqwest::blocking::Response) -> (u16, Value) {
    let status = response.status().as_u16();
    let body = response.text().unwrap_or_default();
    let value = serde_json::from_str(&body).unwrap_or(Value::Null);
    (status, value)
}

fn code_of(body: &Value) -> String {
    body.get("code")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string()
}

fn health(url: &str) -> Result<(), String> {
    let probe = reqwest::blocking::Client::builder()
        .timeout(HEALTH_TIMEOUT)
        .build()
        .map_err(|_| "gateway_unreachable".to_string())?;
    let response = probe
        .get(format!("{url}/health"))
        .send()
        .map_err(|_| "gateway_unreachable".to_string())?;
    if !response.status().is_success() {
        return Err("gateway_unreachable".into());
    }
    let body: Value = response.json().unwrap_or(Value::Null);
    // A Gateway that cannot state its protocol version is not trusted for this build.
    match body.get("gateway_protocol_version").and_then(Value::as_u64) {
        Some(version) if version == GATEWAY_PROTOCOL_VERSION => Ok(()),
        Some(_) => Err("gateway_protocol_mismatch".into()),
        None => Err("gateway_protocol_mismatch".into()),
    }
}

fn store_enrollment(payload: &Value) -> Result<(), String> {
    credential::store_scoped("gateway", &credential_name(), &payload.to_string())
}

fn forget_enrollment() -> Result<(), String> {
    credential::delete_scoped("gateway", &credential_name())
}


/// Public status for the UI. It never contains the installation secret.
#[tauri::command]
pub fn gateway_status() -> Result<Value, String> {
    let default = default_gateway_url();
    match load_enrollment() {
        None => Ok(json!({
            "configured": false,
            "url": Value::Null,
            "default_url": if default.is_empty() { Value::Null } else { json!(default) },
            "installation_id": Value::Null,
            "state": "not_connected",
            "message": "Alex Cloud не подключён.",
        })),
        Some(enrollment) => {
            let url = enrollment["url"].as_str().unwrap_or_default().to_string();
            let installation_id = enrollment["installation_id"].as_str().unwrap_or_default().to_string();
            let (state, message) = match health(&url) {
                Ok(()) => ("connected".to_string(), "Alex Cloud подключён.".to_string()),
                Err(code) => (
                    code.clone(),
                    if code == "gateway_protocol_mismatch" {
                        "Версия Alex Cloud несовместима с этой установкой Alex.".to_string()
                    } else {
                        "Alex Cloud сейчас недоступен.".to_string()
                    },
                ),
            };
            Ok(json!({
                "configured": true,
                "url": url,
                "default_url": if default.is_empty() { Value::Null } else { json!(default) },
                "installation_id": installation_id,
                "state": state,
                "message": message,
            }))
        }
    }
}

/// One-time enrollment with an activation code issued by the Gateway operator.
#[tauri::command]
pub fn gateway_enroll(url: String, activation_code: String) -> Result<Value, String> {
    let target = validate_url(&url)?;
    let code = activation_code.trim();
    if code.len() < 8 || code.len() > MAX_ACTIVATION_CODE {
        return Err("invalid_activation_code".into());
    }
    let response = client()?
        .post(format!("{target}/enroll"))
        .json(&json!({
            "activation_code": code,
            "name": std::env::var("COMPUTERNAME").unwrap_or_default(),
            "platform": "windows",
            "client_version": env!("CARGO_PKG_VERSION"),
            "gateway_protocol_version": GATEWAY_PROTOCOL_VERSION,
        }))
        .send()
        .map_err(|_| "gateway_unreachable".to_string())?;
    let (status, body) = text_of(response);
    if status != 200 {
        let code = code_of(&body);
        return Err(match code.as_str() {
            "activation_code_used" => "activation_code_used".to_string(),
            "activation_code_expired" => "activation_code_expired".to_string(),
            "activation_code_rejected" | "gateway_invalid_request" => {
                "invalid_activation_code".to_string()
            }
            "gateway_protocol_mismatch" => "gateway_protocol_mismatch".to_string(),
            "gateway_rate_limited" => "gateway_rate_limited".to_string(),
            _ if status >= 500 => "gateway_unreachable".to_string(),
            _ => "activation_code_rejected".to_string(),
        });
    }
    let installation_id = body
        .get("installation_id")
        .and_then(Value::as_str)
        .ok_or_else(|| "gateway_unreachable".to_string())?;
    let secret = body
        .get("installation_secret")
        .and_then(Value::as_str)
        .ok_or_else(|| "gateway_unreachable".to_string())?;
    if body.get("gateway_protocol_version").and_then(Value::as_u64) != Some(GATEWAY_PROTOCOL_VERSION) {
        return Err("gateway_protocol_mismatch".into());
    }
    let enrollment = json!({
        "url": target,
        "installation_id": installation_id,
        "installation_secret": secret,
        "enrolled_at": crate::backend::uuid_lite::UuidLite::instance(),
    });
    store_enrollment(&enrollment)?;
    Ok(json!({
        "configured": true,
        "url": target,
        "installation_id": installation_id,
    }))
}

/// Explicit disconnect: revoke server-side (best effort) and forget locally.
#[tauri::command]
pub fn gateway_disconnect() -> Result<Value, String> {
    if let Some(enrollment) = load_enrollment() {
        let url = enrollment["url"].as_str().unwrap_or_default().to_string();
        if let Ok(http) = client() {
            let token = http
                .post(format!("{url}/auth/token"))
                .json(&json!({
                    "installation_id": enrollment["installation_id"],
                    "installation_secret": enrollment["installation_secret"],
                    "gateway_protocol_version": GATEWAY_PROTOCOL_VERSION,
                }))
                .send()
                .ok()
                .and_then(|response| response.json::<Value>().ok())
                .and_then(|body| body.get("access_token").and_then(Value::as_str).map(str::to_string));
            if let Some(token) = token {
                let _ = http
                    .post(format!("{url}/auth/revoke"))
                    .bearer_auth(token)
                    .json(&json!({"reason": "desktop_disconnect"}))
                    .send();
            }
        }
    }
    forget_enrollment()?;
    Ok(json!({"configured": false}))
}

/// Environment for the owned backend. Shared mode is selected here — by installation
/// state and build configuration, never by a user-facing autonomy setting. The RunPod
/// credential of the private/direct path is left untouched and is ignored in shared mode.
pub fn gateway_env() -> Vec<(String, String)> {
    match load_enrollment() {
        Some(enrollment) => vec![
            ("ALEX_AI_MODE".into(), "shared".into()),
            (
                "ALEX_GATEWAY_URL".into(),
                enrollment["url"].as_str().unwrap_or_default().into(),
            ),
            (
                "ALEX_GATEWAY_INSTALLATION_ID".into(),
                enrollment["installation_id"].as_str().unwrap_or_default().into(),
            ),
            (
                "ALEX_GATEWAY_INSTALLATION_SECRET".into(),
                enrollment["installation_secret"].as_str().unwrap_or_default().into(),
            ),
        ],
        None => match std::env::var("ALEX_AI_MODE") {
            Ok(mode) if !mode.trim().is_empty() => vec![("ALEX_AI_MODE".into(), mode)],
            _ => Vec::new(),
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::credential::TEST_ENV_LOCK;
    use std::fs;
    use std::path::PathBuf;

    fn isolated() -> (PathBuf, Option<String>, Option<String>) {
        let previous = std::env::var("ALEX_DEVICE_DIR").ok();
        let previous_name = std::env::var("ALEX_GATEWAY_CREDENTIAL_NAME").ok();
        let temp = std::env::temp_dir().join(format!("alex-gateway-test-{}", unique()));
        let _ = fs::create_dir_all(&temp);
        std::env::set_var("ALEX_DEVICE_DIR", &temp);
        // A distinct credential name keeps the suite away from the real installation
        // entry, which lives in the machine-wide Windows Credential Manager.
        std::env::set_var("ALEX_GATEWAY_CREDENTIAL_NAME", format!("test-{}", unique()));
        (temp, previous, previous_name)
    }

    fn unique() -> String {
        use std::time::{SystemTime, UNIX_EPOCH};
        format!("{:x}", SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos())
    }

    fn restore(previous: Option<String>, previous_name: Option<String>) {
        match previous {
            Some(value) => std::env::set_var("ALEX_DEVICE_DIR", value),
            None => std::env::remove_var("ALEX_DEVICE_DIR"),
        }
        match previous_name {
            Some(value) => std::env::set_var("ALEX_GATEWAY_CREDENTIAL_NAME", value),
            None => std::env::remove_var("ALEX_GATEWAY_CREDENTIAL_NAME"),
        }
    }

    #[test]
    fn remote_plaintext_gateway_is_refused() {
        assert_eq!(validate_url("http://gateway.example").unwrap_err(), "insecure_gateway_url");
        assert!(validate_url("http://127.0.0.1:9000").is_ok());
        assert!(validate_url("http://localhost:9000").is_ok());
        assert!(validate_url("https://gateway.example").is_ok());
        assert_eq!(validate_url("ftp://gateway.example").unwrap_err(), "invalid_url");
        assert_eq!(validate_url("").unwrap_err(), "invalid_url");
        assert_eq!(validate_url("https://user:pass@gateway.example").unwrap_err(), "invalid_url");
        assert_eq!(validate_url("https://gateway.example/?key=x").unwrap_err(), "invalid_url");
    }

    #[test]
    fn trailing_slash_is_normalized() {
        assert_eq!(validate_url("https://gateway.example/").unwrap(), "https://gateway.example");
    }

    #[test]
    fn status_without_enrollment_is_not_connected_and_has_no_secret() {
        let _guard = TEST_ENV_LOCK.lock().unwrap();
        let (temp, previous, previous_name) = isolated();
        let status = gateway_status().unwrap();
        assert_eq!(status["configured"], false);
        assert_eq!(status["state"], "not_connected");
        assert!(status.get("installation_secret").is_none());
        restore(previous, previous_name);
        let _ = fs::remove_dir_all(&temp);
    }

    #[test]
    fn enrollment_is_stored_securely_and_never_returned() {
        let _guard = TEST_ENV_LOCK.lock().unwrap();
        let (temp, previous, previous_name) = isolated();
        store_enrollment(&json!({
                "url": "https://gateway.example",
                "installation_id": "11111111-2222-3333-4444-555555555555",
                "installation_secret": "secret-value-not-for-the-ui",
        }))
        .unwrap();
        let status = gateway_status().unwrap();
        assert_eq!(status["configured"], true);
        assert_eq!(status["url"], "https://gateway.example");
        assert_eq!(status["installation_id"], "11111111-2222-3333-4444-555555555555");
        let rendered = status.to_string();
        assert!(!rendered.contains("secret-value-not-for-the-ui"));
        restore(previous, previous_name);
        let _ = fs::remove_dir_all(&temp);
    }

    #[test]
    fn enrollment_environment_makes_the_backend_shared() {
        let _guard = TEST_ENV_LOCK.lock().unwrap();
        let (temp, previous, previous_name) = isolated();
        store_enrollment(&json!({
                "url": "https://gateway.example",
                "installation_id": "abc",
                "installation_secret": "installation-secret-value",
        }))
        .unwrap();
        let env = gateway_env();
        let map: std::collections::HashMap<_, _> = env.into_iter().collect();
        assert_eq!(map.get("ALEX_AI_MODE").map(String::as_str), Some("shared"));
        assert_eq!(
            map.get("ALEX_GATEWAY_URL").map(String::as_str),
            Some("https://gateway.example")
        );
        assert_eq!(
            map.get("ALEX_GATEWAY_INSTALLATION_SECRET").map(String::as_str),
            Some("installation-secret-value")
        );
        restore(previous, previous_name);
        let _ = fs::remove_dir_all(&temp);
    }

    #[test]
    fn without_enrollment_the_direct_mode_is_default() {
        let _guard = TEST_ENV_LOCK.lock().unwrap();
        let (temp, previous, previous_name) = isolated();
        let mode = std::env::var("ALEX_AI_MODE").ok();
        std::env::remove_var("ALEX_AI_MODE");
        assert!(gateway_env().is_empty());
        std::env::set_var("ALEX_AI_MODE", "shared");
        assert_eq!(gateway_env()[0].0, "ALEX_AI_MODE");
        match mode {
            Some(value) => std::env::set_var("ALEX_AI_MODE", value),
            None => std::env::remove_var("ALEX_AI_MODE"),
        }
        restore(previous, previous_name);
        let _ = fs::remove_dir_all(&temp);
    }

    #[test]
    fn disconnect_removes_the_credential_even_when_the_gateway_is_unreachable() {
        let _guard = TEST_ENV_LOCK.lock().unwrap();
        let (temp, previous, previous_name) = isolated();
        store_enrollment(&json!({
                "url": "https://127.0.0.1:9",
                "installation_id": "abc",
                "installation_secret": "installation-secret-value",
        }))
        .unwrap();
        let result = gateway_disconnect().unwrap();
        assert_eq!(result["configured"], false);
        assert!(credential::load_scoped("gateway", &credential_name()).is_none());
        restore(previous, previous_name);
        let _ = fs::remove_dir_all(&temp);
    }

    #[test]
    fn gateway_scope_is_not_the_provider_scope() {
        assert_eq!(
            credential::scoped_target("gateway", "installation").unwrap(),
            "Alex LLM/gateway/installation"
        );
        assert_eq!(
            credential::scoped_target("provider", "runpod").unwrap(),
            "Alex LLM/provider/runpod"
        );
    }
}
