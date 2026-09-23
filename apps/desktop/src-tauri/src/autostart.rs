//! «Запускать Canalla вместе с Windows»: one per-user value under the documented `Run` key.
//!
//! `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` is how Windows documents a per-user login
//! start: no administrator rights, no service, no scheduled task, no shell script and nothing that
//! polls in the background. The operating system stays the source of truth — every write is read
//! back and every failure is reported, so the toggle can never show a state the machine does not
//! actually have.
//!
//! A development build never registers anything: the machine an operator works on is not a place
//! for the product's login entry, and a test that writes to the operator's own `Run` key would be
//! worse than no test at all.

use std::path::Path;

use serde::Serialize;
use windows::core::PCWSTR;
use windows::Win32::Foundation::{ERROR_FILE_NOT_FOUND, ERROR_SUCCESS, WIN32_ERROR};
use windows::Win32::System::Registry::{
    RegCloseKey, RegCreateKeyExW, RegDeleteValueW, RegOpenKeyExW, RegQueryValueExW, RegSetValueExW,
    HKEY, HKEY_CURRENT_USER, KEY_QUERY_VALUE, KEY_SET_VALUE, REG_OPTION_NON_VOLATILE, REG_SAM_FLAGS,
    REG_SZ, REG_VALUE_TYPE,
};

/// The name the product uses for its login entry, its shortcuts and its uninstall entry.
pub const VALUE_NAME: &str = "Canalla LLM";
const RUN_KEY: &str = r"Software\Microsoft\Windows\CurrentVersion\Run";
/// Tests use their own key: they must never touch the operator's own login entry.
#[cfg(test)]
const TEST_KEY: &str = r"Software\Canalla LLM\autostart-test";

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct AutostartStatus {
    /// Whether this build may register at all (a development build may not).
    pub supported: bool,
    /// What the operating system currently has, read back from the registry.
    pub enabled: bool,
    /// The registered command line, when there is one.
    pub command: Option<String>,
    /// Set when the registry could not be read or written; the UI says so instead of guessing.
    pub error: Option<String>,
}

impl AutostartStatus {
    fn unsupported() -> Self {
        Self {
            supported: false,
            enabled: false,
            command: None,
            error: None,
        }
    }
}

fn wide(text: &str) -> Vec<u16> {
    text.encode_utf16().chain(std::iter::once(0)).collect()
}

fn registry_error(status: WIN32_ERROR) -> String {
    format!("autostart_registry_failed:{}", status.0)
}

struct RegistryKey(HKEY);

impl Drop for RegistryKey {
    fn drop(&mut self) {
        unsafe {
            let _ = RegCloseKey(self.0);
        }
    }
}

/// `Ok(None)` when the key does not exist at all — "nothing registered" is not a failure.
fn open_key(path: &str, access: REG_SAM_FLAGS) -> Result<Option<RegistryKey>, String> {
    let path = wide(path);
    let mut handle = HKEY::default();
    let status =
        unsafe { RegOpenKeyExW(HKEY_CURRENT_USER, PCWSTR(path.as_ptr()), 0, access, &mut handle) };
    match status {
        ERROR_SUCCESS => Ok(Some(RegistryKey(handle))),
        ERROR_FILE_NOT_FOUND => Ok(None),
        other => Err(registry_error(other)),
    }
}

fn create_key(path: &str) -> Result<RegistryKey, String> {
    let path = wide(path);
    let mut handle = HKEY::default();
    let status = unsafe {
        RegCreateKeyExW(
            HKEY_CURRENT_USER,
            PCWSTR(path.as_ptr()),
            0,
            PCWSTR::null(),
            REG_OPTION_NON_VOLATILE,
            KEY_SET_VALUE | KEY_QUERY_VALUE,
            None,
            &mut handle,
            None,
        )
    };
    if status != ERROR_SUCCESS {
        return Err(registry_error(status));
    }
    Ok(RegistryKey(handle))
}

fn read_value(key_path: &str, name: &str) -> Result<Option<String>, String> {
    let Some(key) = open_key(key_path, KEY_QUERY_VALUE)? else {
        return Ok(None);
    };
    let name = wide(name);
    let mut kind = REG_VALUE_TYPE(0);
    let mut size = 0u32;
    let status = unsafe {
        RegQueryValueExW(
            key.0,
            PCWSTR(name.as_ptr()),
            None,
            Some(&mut kind),
            None,
            Some(&mut size),
        )
    };
    if status == ERROR_FILE_NOT_FOUND {
        return Ok(None);
    }
    if status != ERROR_SUCCESS {
        return Err(registry_error(status));
    }
    let mut buffer = vec![0u8; size as usize];
    let status = unsafe {
        RegQueryValueExW(
            key.0,
            PCWSTR(name.as_ptr()),
            None,
            Some(&mut kind),
            Some(buffer.as_mut_ptr()),
            Some(&mut size),
        )
    };
    if status != ERROR_SUCCESS {
        return Err(registry_error(status));
    }
    let units: Vec<u16> = buffer
        .chunks_exact(2)
        .map(|pair| u16::from_le_bytes([pair[0], pair[1]]))
        .collect();
    Ok(Some(
        String::from_utf16_lossy(&units)
            .trim_end_matches('\0')
            .to_string(),
    ))
}

fn write_value(key_path: &str, name: &str, value: &str) -> Result<(), String> {
    let key = create_key(key_path)?;
    let name = wide(name);
    let mut data: Vec<u8> = Vec::new();
    for unit in value.encode_utf16().chain(std::iter::once(0)) {
        data.extend_from_slice(&unit.to_le_bytes());
    }
    let status = unsafe { RegSetValueExW(key.0, PCWSTR(name.as_ptr()), 0, REG_SZ, Some(&data)) };
    if status != ERROR_SUCCESS {
        return Err(registry_error(status));
    }
    Ok(())
}

/// Removing what is not there is not an error: an uninstall must not fail on a clean machine.
fn delete_value(key_path: &str, name: &str) -> Result<(), String> {
    let Some(key) = open_key(key_path, KEY_SET_VALUE)? else {
        return Ok(());
    };
    let name = wide(name);
    let status = unsafe { RegDeleteValueW(key.0, PCWSTR(name.as_ptr())) };
    if status != ERROR_SUCCESS && status != ERROR_FILE_NOT_FOUND {
        return Err(registry_error(status));
    }
    Ok(())
}

/// The command line Windows will run at login: the executable, quoted, because the install
/// directory is `…\Programs\Canalla LLM`.
pub fn command_line(executable: &Path) -> String {
    format!("\"{}\"", executable.display())
}

fn current_command() -> Result<String, String> {
    let executable = std::env::current_exe().map_err(|error| error.to_string())?;
    Ok(command_line(&executable))
}

/// A development build must not put the product into the operator's login.
pub fn supported() -> bool {
    !cfg!(debug_assertions)
}

fn register_in(key: &str) -> Result<String, String> {
    let command = current_command()?;
    write_value(key, VALUE_NAME, &command)?;
    // Read back before claiming anything.
    match read_value(key, VALUE_NAME)? {
        Some(found) if found == command => Ok(command),
        _ => Err("autostart_not_applied".to_string()),
    }
}

fn unregister_in(key: &str) -> Result<(), String> {
    delete_value(key, VALUE_NAME)?;
    match read_value(key, VALUE_NAME)? {
        None => Ok(()),
        Some(_) => Err("autostart_not_removed".to_string()),
    }
}

pub fn status() -> AutostartStatus {
    if !supported() {
        return AutostartStatus::unsupported();
    }
    match read_value(RUN_KEY, VALUE_NAME) {
        Ok(Some(command)) => AutostartStatus {
            supported: true,
            enabled: true,
            command: Some(command),
            error: None,
        },
        Ok(None) => AutostartStatus {
            supported: true,
            enabled: false,
            command: None,
            error: None,
        },
        Err(error) => AutostartStatus {
            supported: true,
            enabled: false,
            command: None,
            error: Some(error),
        },
    }
}

pub fn set_enabled(enabled: bool) -> Result<AutostartStatus, String> {
    if !supported() {
        return Err("autostart_unsupported".to_string());
    }
    if enabled {
        register_in(RUN_KEY)?;
    } else {
        unregister_in(RUN_KEY)?;
    }
    let status = status();
    if status.enabled != enabled {
        return Err(status
            .error
            .clone()
            .unwrap_or_else(|| "autostart_not_applied".to_string()));
    }
    Ok(status)
}

#[tauri::command]
pub fn autostart_status() -> AutostartStatus {
    status()
}

#[tauri::command]
pub fn set_autostart(enabled: bool) -> Result<AutostartStatus, String> {
    set_enabled(enabled)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    /// One key, one test at a time: the tests share the registry they write to.
    static TEST_LOCK: Mutex<()> = Mutex::new(());

    #[test]
    fn a_development_build_never_registers_itself() {
        // `cargo test` builds in debug: the operator's machine must not gain a login entry from
        // running the test suite.
        assert!(!supported());
        assert_eq!(status(), AutostartStatus::unsupported());
        assert_eq!(set_enabled(true), Err("autostart_unsupported".to_string()));
    }

    #[test]
    fn the_registered_command_line_is_the_quoted_executable() {
        assert_eq!(
            command_line(Path::new(r"C:\Users\me\AppData\Local\Programs\Canalla LLM\alex-llm.exe")),
            r#""C:\Users\me\AppData\Local\Programs\Canalla LLM\alex-llm.exe""#
        );
    }

    #[test]
    fn autostart_round_trips_through_the_registry() {
        let _guard = TEST_LOCK.lock().unwrap();
        unregister_in(TEST_KEY).unwrap();
        assert_eq!(read_value(TEST_KEY, VALUE_NAME).unwrap(), None);

        let command = register_in(TEST_KEY).unwrap();
        assert!(command.starts_with('"') && command.ends_with('"'), "{command}");
        assert_eq!(read_value(TEST_KEY, VALUE_NAME).unwrap(), Some(command));

        unregister_in(TEST_KEY).unwrap();
        assert_eq!(read_value(TEST_KEY, VALUE_NAME).unwrap(), None);
        // An uninstall on a machine where nothing was ever registered must not fail.
        unregister_in(TEST_KEY).unwrap();
    }

    #[test]
    fn writing_twice_keeps_exactly_one_value() {
        let _guard = TEST_LOCK.lock().unwrap();
        let first = register_in(TEST_KEY).unwrap();
        let second = register_in(TEST_KEY).unwrap();
        assert_eq!(first, second);
        assert_eq!(read_value(TEST_KEY, VALUE_NAME).unwrap(), Some(second));
        unregister_in(TEST_KEY).unwrap();
    }
}
