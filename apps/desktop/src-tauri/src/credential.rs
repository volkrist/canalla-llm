//! Device credential storage. Windows Credential Manager first; DPAPI file fallback.

use std::fs;
use std::path::PathBuf;

const TARGET: &str = "Alex LLM/device-credential";

fn data_dir() -> PathBuf {
    let root = std::env::var("LOCALAPPDATA").unwrap_or_else(|_| ".".into());
    let dir = PathBuf::from(root).join("Alex LLM");
    let _ = fs::create_dir_all(&dir);
    dir
}

fn fallback_path() -> PathBuf {
    data_dir().join("device.cred.dpapi")
}

pub fn store(value: &str) -> Result<(), String> {
    if cred_write(TARGET, value).is_ok() {
        return Ok(());
    }
    dpapi_write(value)
}

pub fn load() -> Option<String> {
    cred_read(TARGET).ok().or_else(dpapi_read)
}

pub fn delete() -> Result<(), String> {
    let _ = cred_delete(TARGET);
    let _ = fs::remove_file(fallback_path());
    Ok(())
}

pub fn storage_kind() -> &'static str {
    if cred_read(TARGET).is_ok() {
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
        fs::write(fallback_path(), bytes).map_err(|e| e.to_string())?;
        if !out_blob.pbData.is_null() {
            windows::Win32::Foundation::LocalFree(windows::Win32::Foundation::HLOCAL(
                out_blob.pbData as *mut std::ffi::c_void,
            ));
        }
    }
    Ok(())
}

fn dpapi_read() -> Option<String> {
    use windows::Win32::Security::Cryptography::{CryptUnprotectData, CRYPT_INTEGER_BLOB};
    let mut bytes = fs::read(fallback_path()).ok()?;
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
