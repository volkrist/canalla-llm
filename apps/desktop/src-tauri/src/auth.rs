//! Persistent device sessions, first-owner bootstrap and provider secrets.
//!
//! Secrets live only in the OS credential store (`Alex LLM/session/{id}`,
//! `Alex LLM/provider/{name}`). The webview receives only short-lived access
//! tokens and non-secret identifiers; raw refresh secrets never cross the
//! JS boundary. The refresh secret is rotated on every restore, and the
//! rotated credential is written back before the result is returned.

use std::fs;
use std::path::PathBuf;
use std::time::Duration;

use serde_json::{json, Value};

const PROVIDERS: &[(&str, &str)] = &[("runpod", "RUNPOD_API_KEY")];

fn runtime_dir() -> PathBuf {
    crate::backend::data_root().join("runtime")
}

fn install_id_path() -> PathBuf {
    runtime_dir().join("install.id")
}

fn session_id_path() -> PathBuf {
    runtime_dir().join("session.id")
}

/// Stable per-installation device identity. Non-secret. Survives reinstall
/// because the data root is preserved.
pub fn install_id() -> String {
    let path = install_id_path();
    if let Ok(existing) = fs::read_to_string(&path) {
        let trimmed = existing.trim().to_string();
        if trimmed.len() >= 16 {
            return trimmed;
        }
    }
    let id = crate::backend::uuid_lite::UuidLite::instance();
    let _ = fs::create_dir_all(path.parent().unwrap());
    let _ = fs::write(&path, &id);
    id
}

fn current_session_id() -> Option<String> {
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

fn clear_local_session() {
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

async fn auth_post(backend_url: &str, path: &str, payload: Value) -> Result<Value, String> {
    let client = http_client()?;
    let response = client
        .post(format!("{backend_url}{path}"))
        .header("X-Alex-Device-Id", install_id())
        .json(&payload)
        .send()
        .await
        .map_err(|_| "backend_unreachable".to_string())?;
    if !response.status().is_success() {
        let detail = response
            .json::<Value>()
            .await
            .ok()
            .and_then(|body| body.get("detail").cloned());
        return Err(match detail {
            Some(Value::String(text)) if !text.is_empty() => format!("backend_rejected:{text}"),
            _ => "backend_rejected".to_string(),
        });
    }
    response.json().await.map_err(|error| error.to_string())
}

/// Store the session credential issued after login/register/bootstrap and
/// remember which session is active for this installation.
fn store_session(body: &Value) -> Result<Value, String> {
    let session_id = body
        .get("session_id")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or("no_session_id")?;
    let secret = body
        .get("refresh_secret")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or("no_refresh_secret")?;
    crate::credential::store_scoped("session", session_id, secret)?;
    let _ = fs::create_dir_all(session_id_path().parent().unwrap());
    fs::write(session_id_path(), session_id).map_err(|error| error.to_string())?;
    Ok(json!({
        "access_token": body.get("access_token").cloned().unwrap_or(Value::Null),
        "user": body.get("user").cloned().unwrap_or(Value::Null),
        "session_id": session_id,
    }))
}

fn valid_password(password: &str) -> bool {
    (10..=128).contains(&password.chars().count())
}

#[tauri::command]
pub async fn auth_login(backend_url: String, email: String, password: String) -> Result<Value, String> {
    if !valid_password(&password) {
        return Err("invalid_credentials".into());
    }
    let body = auth_post(
        &backend_url,
        "/auth/login",
        json!({"email": email, "password": password}),
    )
    .await?;
    store_session(&body)
}

#[tauri::command]
pub async fn auth_register(
    backend_url: String,
    email: String,
    password: String,
) -> Result<Value, String> {
    if !valid_password(&password) {
        return Err("invalid_credentials".into());
    }
    let body = auth_post(
        &backend_url,
        "/auth/register",
        json!({"email": email, "password": password}),
    )
    .await?;
    store_session(&body)
}

#[tauri::command]
pub async fn auth_bootstrap(
    backend_url: String,
    email: String,
    password: String,
    display_name: String,
) -> Result<Value, String> {
    if !valid_password(&password) {
        return Err("invalid_credentials".into());
    }
    // Local-installation proof: the runtime token shared with the owned
    // backend process. Generic web content never holds it.
    let root = crate::backend::data_root();
    let token = crate::backend::load_or_create_runtime_token(&root)?;
    let client = http_client()?;
    let response = client
        .post(format!("{backend_url}/auth/bootstrap"))
        .header("X-Alex-Runtime-Token", token)
        .header("X-Alex-Device-Id", install_id())
        .json(&json!({
            "email": email,
            "password": password,
            "display_name": display_name,
        }))
        .send()
        .await
        .map_err(|_| "backend_unreachable".to_string())?;
    if !response.status().is_success() {
        let detail = response
            .json::<Value>()
            .await
            .ok()
            .and_then(|body| body.get("detail").cloned());
        return Err(match detail {
            Some(Value::String(text)) if !text.is_empty() => format!("backend_rejected:{text}"),
            _ => "backend_rejected".to_string(),
        });
    }
    let body: Value = response.json().await.map_err(|error| error.to_string())?;
    store_session(&body)
}

#[tauri::command]
pub async fn auth_restore(backend_url: String) -> Result<Value, String> {
    let Some(session_id) = current_session_id() else {
        return Err("no_session".into());
    };
    let Some(secret) = session_secret(&session_id) else {
        clear_local_session();
        return Err("no_session".into());
    };
    let client = http_client()?;
    let response = client
        .post(format!("{backend_url}/auth/refresh"))
        .header("X-Alex-Device-Id", install_id())
        .json(&json!({"session_id": session_id, "refresh_secret": secret}))
        .send()
        .await
        .map_err(|_| "backend_unreachable".to_string())?;
    if !response.status().is_success() {
        let code = response.status();
        if code == reqwest::StatusCode::UNAUTHORIZED || code == reqwest::StatusCode::CONFLICT {
            // Revoked / expired / unknown session: drop the local credential
            // so the next launch goes to the auth screen instead of retrying.
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
    // Rotation: persist the new secret before returning, so a concurrent
    // consumer never replays the consumed one.
    crate::credential::store_scoped("session", &session_id, fresh_secret)?;
    Ok(json!({
        "access_token": body.get("access_token").cloned().unwrap_or(Value::Null),
        "user": body.get("user").cloned().unwrap_or(Value::Null),
        "session_id": session_id,
    }))
}

#[tauri::command]
pub async fn auth_logout(backend_url: String) -> Result<Value, String> {
    let mut revoked = false;
    if let Some(session_id) = current_session_id() {
        if let Some(secret) = session_secret(&session_id) {
            let client = http_client()?;
            let _ = client
                .post(format!("{backend_url}/auth/revoke"))
                .json(&json!({"session_id": session_id, "refresh_secret": secret}))
                .send()
                .await;
            revoked = true;
        }
    }
    clear_local_session();
    Ok(json!({"revoked": revoked, "cleared": true}))
}

#[tauri::command]
pub fn session_status() -> Value {
    match current_session_id() {
        Some(id) => json!({
            "has_session": true,
            "session_id": id,
            "storage": crate::credential::storage_kind(),
        }),
        None => json!({"has_session": false, "storage": crate::credential::storage_kind()}),
    }
}

fn provider_name(name: &str) -> Result<(), String> {
    if PROVIDERS.iter().any(|(key, _)| *key == name) {
        Ok(())
    } else {
        Err("unknown_provider".into())
    }
}

#[tauri::command]
pub fn provider_secret_configured(name: String) -> Result<Value, String> {
    provider_name(&name)?;
    Ok(json!({
        "configured": crate::credential::load_scoped("provider", &name)
            .map(|value| !value.is_empty())
            .unwrap_or(false),
    }))
}

#[tauri::command]
pub fn set_provider_secret(name: String, secret: String) -> Result<Value, String> {
    provider_name(&name)?;
    let value = secret.trim();
    if value.is_empty() || value.len() > 512 {
        return Err("invalid_secret".into());
    }
    crate::credential::store_scoped("provider", &name, value)?;
    Ok(json!({"configured": true}))
}

#[tauri::command]
pub fn delete_provider_secret(name: String) -> Result<Value, String> {
    provider_name(&name)?;
    crate::credential::delete_scoped("provider", &name)?;
    Ok(json!({"configured": false}))
}

/// Provider secrets to pass into the owned backend environment at spawn.
/// Returns (env_name, value) pairs. Never logged, never returned to the UI.
pub fn provider_env() -> Vec<(String, String)> {
    PROVIDERS
        .iter()
        .filter_map(|(name, env_name)| {
            crate::credential::load_scoped("provider", name)
                .filter(|value| !value.is_empty())
                .map(|value| (env_name.to_string(), value))
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::backend::uuid_lite::UuidLite;

    fn isolated_root() -> (PathBuf, Option<String>) {
        let previous = std::env::var("ALEX_LLM_DATA_DIR").ok();
        let temp = std::env::temp_dir().join(format!("alex-auth-test-{}", UuidLite::instance()));
        std::env::set_var("ALEX_LLM_DATA_DIR", &temp);
        (temp, previous)
    }

    fn restore_root(previous: Option<String>) {
        match previous {
            Some(value) => std::env::set_var("ALEX_LLM_DATA_DIR", value),
            None => std::env::remove_var("ALEX_LLM_DATA_DIR"),
        }
    }

    #[test]
    fn install_id_is_stable_across_reads() {
        let _guard = crate::credential::TEST_ENV_LOCK.lock().unwrap();
        let (temp, previous) = isolated_root();
        let first = install_id();
        let second = install_id();
        assert_eq!(first, second);
        assert!(first.len() >= 16);
        assert_eq!(
            fs::read_to_string(install_id_path()).unwrap().trim(),
            first
        );
        restore_root(previous);
        let _ = fs::remove_dir_all(&temp);
    }

    #[test]
    fn session_pointer_validation_rejects_junk() {
        let _guard = crate::credential::TEST_ENV_LOCK.lock().unwrap();
        let (temp, previous) = isolated_root();
        let _ = fs::create_dir_all(runtime_dir());
        fs::write(session_id_path(), "not a valid id!").unwrap();
        assert!(current_session_id().is_none());
        fs::write(session_id_path(), "abc-def.123").unwrap();
        assert_eq!(current_session_id().as_deref(), Some("abc-def.123"));
        restore_root(previous);
        let _ = fs::remove_dir_all(&temp);
    }

    #[test]
    fn provider_whitelist_rejects_unknown_names() {
        assert!(provider_name("runpod").is_ok());
        assert!(provider_name("tinyfish").is_err());
        assert!(provider_name("").is_err());
        assert!(provider_name("RUNPOD").is_err());
    }

    #[test]
    fn password_bounds_are_enforced() {
        assert!(!valid_password("short"));
        assert!(valid_password(&"a".repeat(10)));
        assert!(valid_password(&"a".repeat(128)));
        assert!(!valid_password(&"a".repeat(129)));
    }
}
