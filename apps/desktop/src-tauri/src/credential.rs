//! Device credential storage. Windows Credential Manager first; DPAPI file fallback.

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

pub fn data_dir() -> PathBuf {
    if let Ok(dir) = std::env::var("ALEX_DEVICE_DIR") {
        let path = PathBuf::from(dir);
        if !path.as_os_str().is_empty() {
            let _ = fs::create_dir_all(&path);
            return path;
        }
    }
    let root = std::env::var("LOCALAPPDATA").unwrap_or_else(|_| ".".into());
    let dir = PathBuf::from(root).join("Alex LLM");
    let _ = fs::create_dir_all(&dir);
    dir
}

fn fallback_path() -> PathBuf {
    data_dir().join("device.cred.dpapi")
}

pub fn store(value: &str) -> Result<(), String> {
    if cred_write(&credential_target(), value).is_ok() {
        return Ok(());
    }
    dpapi_write(value)
}

pub fn load() -> Option<String> {
    cred_read(&credential_target()).ok().or_else(dpapi_read)
}

pub fn delete() -> Result<(), String> {
    let _ = cred_delete(&credential_target());
    let _ = fs::remove_file(fallback_path());
    Ok(())
}

pub fn storage_kind() -> &'static str {
    if cred_read(&credential_target()).is_ok() {
        "windows_credential_manager"
    } else if fallback_path().exists() {
        "dpapi_file_fallback"
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

fn scoped_target(scope: &str, name: &str) -> Result<String, String> {
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
/// Falls back to a DPAPI file in the data root when Credential Manager fails.
pub fn store_scoped(scope: &str, name: &str, value: &str) -> Result<(), String> {
    let target = scoped_target(scope, name)?;
    if cred_write(&target, value).is_ok() {
        return Ok(());
    }
    dpapi_write_to(&scoped_fallback_path(scope, name), value)
}

pub fn load_scoped(scope: &str, name: &str) -> Option<String> {
    let target = scoped_target(scope, name).ok()?;
    cred_read(&target)
        .ok()
        .or_else(|| dpapi_read_from(&scoped_fallback_path(scope, name)))
}

pub fn delete_scoped(scope: &str, name: &str) -> Result<(), String> {
    let target = scoped_target(scope, name)?;
    let _ = cred_delete(&target);
    let _ = fs::remove_file(scoped_fallback_path(scope, name));
    Ok(())
}

fn scoped_fallback_path(scope: &str, name: &str) -> PathBuf {
    let dir = data_dir().join("runtime");
    let _ = fs::create_dir_all(&dir);
    dir.join(format!("cred-{scope}-{name}.dpapi"))
}

fn user_target(name: &str) -> Result<String, String> {
    valid_reference(name)?;
    Ok(format!("Alex LLM/user/{name}"))
}

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
    cred_write(&target, value)?;
    let mut names = load_names();
    if !names.iter().any(|item| item == name) {
        names.push(name.to_string());
        save_names(&names);
    }
    Ok(())
}

pub fn load_named(name: &str) -> Option<String> {
    let target = user_target(name).ok()?;
    cred_read(&target).ok()
}

pub fn delete_named(name: &str) -> Result<(), String> {
    let target = user_target(name)?;
    let _ = cred_delete(&target);
    let names: Vec<String> = load_names().into_iter().filter(|item| item != name).collect();
    save_names(&names);
    Ok(())
}

pub fn list_named() -> Vec<String> {
    load_names()
}

fn cred_write(target: &str, value: &str) -> Result<(), String> {
    use windows::Win32::Security::Credentials::{
        CredWriteW, CREDENTIALW, CRED_PERSIST_LOCAL_MACHINE, CRED_TYPE_GENERIC,
    };
    use windows::core::PWSTR;

    let mut target_w: Vec<u16> = target.encode_utf16().chain(std::iter::once(0)).collect();
    let mut blob: Vec<u8> = value.as_bytes().to_vec();
    unsafe {
        let cred = CREDENTIALW {
            Flags: Default::default(),
            Type: CRED_TYPE_GENERIC,
            TargetName: PWSTR(target_w.as_mut_ptr()),
            Comment: PWSTR::null(),
            LastWritten: Default::default(),
            CredentialBlobSize: blob.len() as u32,
            CredentialBlob: blob.as_mut_ptr(),
            Persist: CRED_PERSIST_LOCAL_MACHINE,
            AttributeCount: 0,
            Attributes: std::ptr::null_mut(),
            TargetAlias: PWSTR::null(),
            UserName: PWSTR::null(),
        };
        CredWriteW(&cred, 0).map_err(|e| e.to_string())?;
    }
    Ok(())
}

fn cred_read(target: &str) -> Result<String, String> {
    use windows::Win32::Security::Credentials::{CredFree, CredReadW, CREDENTIALW, CRED_TYPE_GENERIC};
    use windows::core::PCWSTR;

    let target_w: Vec<u16> = target.encode_utf16().chain(std::iter::once(0)).collect();
    unsafe {
        let mut cred: *mut CREDENTIALW = std::ptr::null_mut();
        CredReadW(PCWSTR(target_w.as_ptr()), CRED_TYPE_GENERIC, 0, &mut cred).map_err(|e| e.to_string())?;
        if cred.is_null() {
            return Err("missing".into());
        }
        let blob = std::slice::from_raw_parts((*cred).CredentialBlob, (*cred).CredentialBlobSize as usize);
        let value = String::from_utf8_lossy(blob).into_owned();
        CredFree(cred as *const _);
        Ok(value)
    }
}

fn cred_delete(target: &str) -> Result<(), String> {
    use windows::Win32::Security::Credentials::{CredDeleteW, CRED_TYPE_GENERIC};
    use windows::core::PCWSTR;

    let target_w: Vec<u16> = target.encode_utf16().chain(std::iter::once(0)).collect();
    unsafe {
        CredDeleteW(PCWSTR(target_w.as_ptr()), CRED_TYPE_GENERIC, 0).map_err(|e| e.to_string())?;
    }
    Ok(())
}

fn dpapi_write(value: &str) -> Result<(), String> {
    dpapi_write_to(&fallback_path(), value)
}

fn dpapi_write_to(path: &PathBuf, value: &str) -> Result<(), String> {
    use windows::Win32::Security::Cryptography::{CryptProtectData, CRYPT_INTEGER_BLOB};
    let mut input = value.as_bytes().to_vec();
    let mut in_blob = CRYPT_INTEGER_BLOB {
        cbData: input.len() as u32,
        pbData: input.as_mut_ptr(),
    };
    let mut out_blob = CRYPT_INTEGER_BLOB::default();
    unsafe {
        CryptProtectData(&mut in_blob, None, None, None, None, 0, &mut out_blob).map_err(|e| e.to_string())?;
        let bytes = std::slice::from_raw_parts(out_blob.pbData, out_blob.cbData as usize);
        fs::write(path, bytes).map_err(|e| e.to_string())?;
        if !out_blob.pbData.is_null() {
            windows::Win32::Foundation::LocalFree(windows::Win32::Foundation::HLOCAL(
                out_blob.pbData as *mut std::ffi::c_void,
            ));
        }
    }
    Ok(())
}

fn dpapi_read() -> Option<String> {
    dpapi_read_from(&fallback_path())
}

fn dpapi_read_from(path: &PathBuf) -> Option<String> {
    use windows::Win32::Security::Cryptography::{CryptUnprotectData, CRYPT_INTEGER_BLOB};
    let mut bytes = fs::read(path).ok()?;
    let mut in_blob = CRYPT_INTEGER_BLOB {
        cbData: bytes.len() as u32,
        pbData: bytes.as_mut_ptr(),
    };
    let mut out_blob = CRYPT_INTEGER_BLOB::default();
    unsafe {
        CryptUnprotectData(&mut in_blob, None, None, None, None, 0, &mut out_blob).ok()?;
        let plain = std::slice::from_raw_parts(out_blob.pbData, out_blob.cbData as usize);
        let value = String::from_utf8_lossy(plain).into_owned();
        if !out_blob.pbData.is_null() {
            windows::Win32::Foundation::LocalFree(windows::Win32::Foundation::HLOCAL(
                out_blob.pbData as *mut std::ffi::c_void,
            ));
        }
        Some(value)
    }
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

    #[test]
    fn scoped_credential_roundtrip_and_delete() {
        let _guard = TEST_ENV_LOCK.lock().unwrap();
        let (temp, previous) = isolated_device_dir();
        let name = format!("unit-{}", unique());
        assert!(store_scoped("session", &name, "secret-value").is_ok());
        assert_eq!(load_scoped("session", &name).as_deref(), Some("secret-value"));
        assert!(load_scoped("session", "no-such-name").is_none());
        assert!(load_scoped("provider", &name).is_none());
        delete_scoped("session", &name).unwrap();
        assert!(load_scoped("session", &name).is_none());
        restore_device_dir(previous);
        let _ = fs::remove_dir_all(&temp);
    }
}
