//! Device credential storage.
//!
//! One contract, two stores: Windows Credential Manager (with its DPAPI file fallback) and the Linux
//! Secret Service. Both are the operating system's own idea of "a secret only this user can read";
//! neither is a file we invented, and there is no plaintext path in either.
//!
//! When Linux cannot reach the Secret Service the answer is `secure_storage_unavailable` and the
//! caller refuses to pair. Storing the credential in a JSON file next to the database would make
//! every later encryption pointless, so that is deliberately not an option.

use std::fs;
use std::path::PathBuf;

fn credential_target() -> String {
    let raw = std::env::var("ALEX_DEVICE_CREDENTIAL_TARGET")
        .unwrap_or_else(|_| "Alex LLM/device-credential".into());
    if raw.starts_with("Alex LLM/")
        && raw.len() <= 80
        && raw
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '/' | '-' | '_' | ' ' | '.'))
    {
        raw
    } else {
        "Alex LLM/device-credential".into()
    }
}

/// Where the product's own files live. `ALEX_DEVICE_DIR` moves only this directory's device record in
/// the acceptance runs; the platform decides the real default (`%LOCALAPPDATA%\Alex LLM`,
/// `$XDG_DATA_HOME/alex-llm`), and the Python half uses the same one.
pub fn data_dir() -> PathBuf {
    if let Ok(dir) = std::env::var("ALEX_DEVICE_DIR") {
        let path = PathBuf::from(dir);
        if !path.as_os_str().is_empty() {
            let _ = fs::create_dir_all(&path);
            return path;
        }
    }
    let dir = crate::platform::data_root_default();
    let _ = fs::create_dir_all(&dir);
    dir
}

/// The DPAPI file the Windows store falls back to. On Linux there is no such file: the parameter is
/// part of the shared contract and is ignored there (and a test asserts nothing is written to it).
fn fallback_path() -> PathBuf {
    data_dir().join("device.cred.dpapi")
}

fn scoped_fallback_path(scope: &str, name: &str) -> PathBuf {
    let dir = data_dir().join("runtime");
    let _ = fs::create_dir_all(&dir);
    dir.join(format!("cred-{scope}-{name}.dpapi"))
}

pub fn store(value: &str) -> Result<(), String> {
    crate::platform::secret_write(&credential_target(), value, &fallback_path())
}

pub fn load() -> Option<String> {
    crate::platform::secret_read(&credential_target(), &fallback_path()).ok()
}

pub fn delete() -> Result<(), String> {
    crate::platform::secret_delete(&credential_target(), &fallback_path())
}

/// Which store holds the credential, for the diagnostics the UI shows. `none` means "not stored yet".
pub fn storage_kind() -> &'static str {
    if crate::platform::secret_fallback_in_use(&fallback_path()) {
        "dpapi_file_fallback"
    } else if load().is_some() {
        crate::platform::secret_kind()
    } else {
        "none"
    }
}

fn valid_reference(name: &str) -> Result<(), String> {
    if name.is_empty()
        || name.len() > 80
        || !name
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_' || c == '.')
    {
        return Err("invalid_reference".into());
    }
    Ok(())
}

pub fn scoped_target(scope: &str, name: &str) -> Result<String, String> {
    if scope.is_empty()
        || scope.len() > 32
        || !scope
            .chars()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-')
    {
        return Err("invalid_scope".into());
    }
    valid_reference(name)?;
    Ok(format!("Alex LLM/{scope}/{name}"))
}

/// Scoped credential storage: `Alex LLM/{scope}/{name}` targets.
/// Used for device sessions (`session`) and provider secrets (`provider`).
pub fn store_scoped(scope: &str, name: &str, value: &str) -> Result<(), String> {
    let target = scoped_target(scope, name)?;
    crate::platform::secret_write(&target, value, &scoped_fallback_path(scope, name))
}

pub fn load_scoped(scope: &str, name: &str) -> Option<String> {
    let target = scoped_target(scope, name).ok()?;
    crate::platform::secret_read(&target, &scoped_fallback_path(scope, name)).ok()
}

pub fn delete_scoped(scope: &str, name: &str) -> Result<(), String> {
    let target = scoped_target(scope, name)?;
    crate::platform::secret_delete(&target, &scoped_fallback_path(scope, name))
}

fn user_target(name: &str) -> Result<String, String> {
    valid_reference(name)?;
    Ok(format!("Alex LLM/user/{name}"))
}

/// The list of names a user has stored. It holds references only - never a value - so it stays a
/// plain file that is safe to read.
fn names_path() -> PathBuf {
    data_dir().join("user-credential-names.json")
}

fn load_names() -> Vec<String> {
    fs::read_to_string(names_path())
        .ok()
        .and_then(|text| serde_json::from_str(&text).ok())
        .unwrap_or_default()
}

fn save_names(names: &[String]) {
    let _ = fs::write(names_path(), serde_json::to_vec(names).unwrap_or_default());
}

pub fn store_named(name: &str, value: &str) -> Result<(), String> {
    let target = user_target(name)?;
    crate::platform::secret_write(&target, value, &fallback_path())?;
    let mut names = load_names();
    if !names.iter().any(|item| item == name) {
        names.push(name.to_string());
        save_names(&names);
    }
    Ok(())
}

pub fn load_named(name: &str) -> Option<String> {
    let target = user_target(name).ok()?;
    crate::platform::secret_read(&target, &fallback_path()).ok()
}

pub fn delete_named(name: &str) -> Result<(), String> {
    let target = user_target(name)?;
    crate::platform::secret_delete(&target, &fallback_path())?;
    let names: Vec<String> = load_names().into_iter().filter(|item| item != name).collect();
    save_names(&names);
    Ok(())
}

pub fn list_named() -> Vec<String> {
    load_names()
}

/// Serializes tests that mutate process-global environment variables
/// (ALEX_LLM_DATA_DIR / ALEX_DEVICE_DIR / provider env), which would
/// otherwise race under parallel test execution.
#[cfg(test)]
pub static TEST_ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

#[cfg(test)]
mod tests {
    use super::*;

    fn unique() -> String {
        use std::time::{SystemTime, UNIX_EPOCH};
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|duration| duration.as_nanos())
            .unwrap_or(0);
        format!("{nanos:x}")
    }

    fn isolated_device_dir() -> (PathBuf, Option<String>) {
        let previous = std::env::var("ALEX_DEVICE_DIR").ok();
        let temp = std::env::temp_dir().join(format!("alex-cred-test-{}", unique()));
        let _ = fs::create_dir_all(&temp);
        std::env::set_var("ALEX_DEVICE_DIR", &temp);
        (temp, previous)
    }

    fn restore_device_dir(previous: Option<String>) {
        match previous {
            Some(value) => std::env::set_var("ALEX_DEVICE_DIR", value),
            None => std::env::remove_var("ALEX_DEVICE_DIR"),
        }
    }

    #[test]
    fn scoped_reference_validation_rejects_junk() {
        assert!(store_scoped("bad scope", "x", "v").is_err());
        assert!(store_scoped("UPPER", "x", "v").is_err());
        assert!(store_scoped("", "x", "v").is_err());
        assert!(store_scoped("session", "", "v").is_err());
        assert!(store_scoped("session", "a/b", "v").is_err());
        assert!(store_scoped("session", "has space", "v").is_err());
    }

    /// The same contract on both platforms: what was stored comes back, another scope does not see
    /// it, and deleting really removes it. Where the platform has no usable store the contract is the
    /// typed refusal - never a silent write somewhere else.
    #[test]
    fn scoped_credential_roundtrip_and_delete() {
        let _guard = TEST_ENV_LOCK.lock().unwrap();
        let (temp, previous) = isolated_device_dir();
        let name = format!("unit-{}", unique());
        match store_scoped("session", &name, "secret-value") {
            Ok(()) => {
                assert_eq!(load_scoped("session", &name).as_deref(), Some("secret-value"));
                assert!(load_scoped("session", "no-such-name").is_none());
                assert!(load_scoped("provider", &name).is_none());
                assert_eq!(load_scoped("session", &name).as_deref(), Some("secret-value"));
                delete_scoped("session", &name).unwrap();
                assert!(load_scoped("session", &name).is_none());
            }
            Err(error) => {
                assert_eq!(error, crate::platform::SECURE_STORAGE_UNAVAILABLE);
                // Nothing may be written when the secure store is unavailable.
                let leftovers: Vec<_> = fs::read_dir(&temp)
                    .map(|entries| entries.filter_map(Result::ok).collect())
                    .unwrap_or_default();
                assert!(leftovers.is_empty(), "a secret never lands in the data directory");
            }
        }
        restore_device_dir(previous);
        let _ = fs::remove_dir_all(&temp);
    }

    /// `ALEX_DEVICE_DIR` moves `device.json` only: the device credential is one machine-wide entry
    /// in the platform's own store, so an isolated run (an acceptance harness) must be able to point
    /// it somewhere else or it would overwrite the credential the real installation paired with.
    #[test]
    fn device_credential_target_redirects_the_machine_wide_entry() {
        let _guard = TEST_ENV_LOCK.lock().unwrap();
        let (temp, previous_dir) = isolated_device_dir();
        let previous = std::env::var("ALEX_DEVICE_CREDENTIAL_TARGET").ok();
        let target = format!("Alex LLM/device-credential-unit-{}", unique());
        std::env::set_var("ALEX_DEVICE_CREDENTIAL_TARGET", &target);
        assert_eq!(credential_target(), target);
        match store("isolated-value") {
            Ok(()) => {
                assert_eq!(load().as_deref(), Some("isolated-value"));
                delete().unwrap();
                assert!(load().is_none());
            }
            Err(error) => {
                // No secure store on this platform right now: the redirect is still honoured, and
                // the refusal is typed rather than a fallback write.
                assert_eq!(error, crate::platform::SECURE_STORAGE_UNAVAILABLE);
                assert!(load().is_none());
            }
        }
        assert_eq!(credential_target(), target);
        match previous {
            Some(value) => std::env::set_var("ALEX_DEVICE_CREDENTIAL_TARGET", value),
            None => std::env::remove_var("ALEX_DEVICE_CREDENTIAL_TARGET"),
        }
        restore_device_dir(previous_dir);
        let _ = fs::remove_dir_all(&temp);
    }
}
