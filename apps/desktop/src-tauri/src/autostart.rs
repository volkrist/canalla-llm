//! «Запускать Canalla вместе с Windows» — and, on Linux, «…вместе с системой».
//!
//! Both platforms register a **per-user login start** through the mechanism the operating system
//! documents for it: the `Run` value under `HKCU` on Windows, an XDG autostart entry in
//! `$XDG_CONFIG_HOME/autostart` on Linux. Neither needs an administrator, a service, a scheduled
//! task, a shell script or anything that polls in the background.
//!
//! The operating system stays the source of truth: every write is read back from it, and every
//! failure is reported, so the toggle can never show a state the machine does not actually have.
//!
//! A development build never registers anything: the machine an operator works on is not a place for
//! the product's login entry, and a test that wrote to the operator's own login would be worse than
//! no test at all.

use serde::Serialize;
use std::path::Path;

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct AutostartStatus {
    /// Whether this build and platform may register at all (a development build may not).
    pub supported: bool,
    /// What the operating system currently has, read back from it.
    pub enabled: bool,
    /// The registered command line, when there is one.
    pub command: Option<String>,
    /// Set when the entry could not be read or written; the UI says so instead of guessing.
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

/// A development build must not put the product into the operator's login.
pub fn supported() -> bool {
    !cfg!(debug_assertions) && crate::platform::login_supported()
}

fn current_executable() -> Result<std::path::PathBuf, String> {
    std::env::current_exe().map_err(|error| error.to_string())
}

pub fn status() -> AutostartStatus {
    if !supported() {
        return AutostartStatus::unsupported();
    }
    match crate::platform::login_read() {
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
    let executable = current_executable()?;
    crate::platform::login_write(enabled, &executable)?;
    let status = status();
    if status.enabled != enabled {
        return Err(status
            .error
            .clone()
            .unwrap_or_else(|| "autostart_not_applied".to_string()));
    }
    Ok(status)
}

/// The command line the platform would run at login, for a given executable. Kept here because the
/// two platforms quote differently (a `Run` value versus a desktop-entry `Exec=`).
#[allow(dead_code)]
pub fn command_line(executable: &Path) -> String {
    crate::platform::login_command(executable)
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

    #[test]
    fn a_development_build_never_registers_itself() {
        // `cargo test` builds in debug: the operator's machine must not gain a login entry from
        // running the test suite.
        assert!(!supported());
        assert_eq!(status(), AutostartStatus::unsupported());
        assert_eq!(set_enabled(true), Err("autostart_unsupported".to_string()));
    }
}
