//! The Windows implementation of the platform contract.
//!
//! Most of this is the code that shipped in 1.0/1.1, moved here unchanged: the Job Object that owns
//! the sidecar, the Credential Manager plus its DPAPI file fallback, the registry `Run` value, the
//! Shell known folders, and `ReplaceFileW`. Nothing about the behaviour changed in the move.

use std::collections::HashMap;
use std::ffi::c_void;
use std::fs;
use std::io;
use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command};

const CREATE_NO_WINDOW: u32 = 0x0800_0000;
const CREATE_UNICODE_ENVIRONMENT: u32 = 0x0000_0400;
const CREATE_BREAKAWAY_FROM_JOB: u32 = 0x0100_0000;

/// The name the product uses for its login entry, its shortcuts and its uninstall entry.
pub const LOGIN_VALUE_NAME: &str = "Canalla LLM";
/// The key the product registers in. A test build writes to its own key instead, so running the
/// suite can never touch the operator's own login entry.
#[cfg_attr(test, allow(dead_code))]
const RUN_KEY: &str = r"Software\Microsoft\Windows\CurrentVersion\Run";
/// Tests use their own key: they must never touch the operator's own login entry.
#[cfg(test)]
const TEST_KEY: &str = r"Software\Canalla LLM\autostart-test";

// ------------------------------------------------------------------------------------- ownership

/// A kill-on-close Job Object: dropping the last handle stops every process in the job.
pub struct OwnedJob(pub(crate) OwnedHandle);

fn create_job() -> Result<OwnedJob, String> {
    use windows::Win32::System::JobObjects::{
        CreateJobObjectW, JobObjectExtendedLimitInformation, SetInformationJobObject,
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };
    unsafe {
        let job =
            CreateJobObjectW(None, windows::core::PCWSTR::null()).map_err(|e| e.to_string())?;
        let mut info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            std::ptr::addr_of!(info).cast(),
            std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        )
        .map_err(|e| e.to_string())?;
        Ok(OwnedJob(OwnedHandle::from_raw_handle(job.0)))
    }
}

fn assign_job(job: &OwnedJob, child: &Child) -> Result<(), String> {
    use windows::Win32::Foundation::HANDLE;
    use windows::Win32::System::JobObjects::AssignProcessToJobObject;
    unsafe {
        AssignProcessToJobObject(HANDLE(job.0.as_raw_handle()), HANDLE(child.as_raw_handle()))
            .map_err(|e| e.to_string())
    }
}

/// Take ownership of a child that was spawned with [`configure_spawn`]. The returned handle stops
/// the child and everything it started when it is dropped, so the desktop can never outlive the
/// sidecar it owns.
pub fn own_child(child: &Child) -> Option<OwnedJob> {
    let job = create_job().ok()?;
    assign_job(&job, child).ok()?;
    Some(job)
}

pub fn process_alive(pid: u32) -> bool {
    use windows::Win32::Foundation::{CloseHandle, STILL_ACTIVE};
    use windows::Win32::System::Threading::{
        GetExitCodeProcess, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
    };
    unsafe {
        let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid);
        let Ok(handle) = handle else {
            return false;
        };
        let mut code = 0u32;
        let ok = GetExitCodeProcess(handle, &mut code).is_ok() && code == STILL_ACTIVE.0 as u32;
        let _ = CloseHandle(handle);
        ok
    }
}

pub fn terminate_pid(pid: u32) {
    use windows::Win32::Foundation::CloseHandle;
    use windows::Win32::System::Threading::{OpenProcess, TerminateProcess, PROCESS_TERMINATE};
    unsafe {
        if let Ok(handle) = OpenProcess(PROCESS_TERMINATE, false, pid) {
            let _ = TerminateProcess(handle, 1);
            let _ = CloseHandle(handle);
        }
    }
}

/// Terminates only the process tree rooted at our own spawned PID.
pub fn terminate_tree(pid: u32) {
    let _ = std::process::Command::new("taskkill")
        .args(["/PID", &pid.to_string(), "/T", "/F"])
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status();
}

// ------------------------------------------------------------------------------------ spawning

/// A console window is never wanted, and the child leaves the job it inherited so ours can own it.
pub fn configure_spawn(command: &mut Command) {
    command
        .creation_flags(CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT | CREATE_BREAKAWAY_FROM_JOB);
}

/// The retry without the breakaway flag: a process already inside a job that forbids breakaway
/// cannot leave it, and refusing to start would be worse than sharing one.
pub fn configure_spawn_plain(command: &mut Command) {
    command.creation_flags(CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT);
}

/// The environment a tool the user asked for may see: the machine's own variables, never this
/// application's secrets.
pub fn sanitized_env() -> HashMap<String, String> {
    let system_root = std::env::var("SystemRoot").unwrap_or_else(|_| r"C:\Windows".into());
    let mut env = HashMap::new();
    for key in [
        "SystemRoot",
        "SystemDrive",
        "windir",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "LOCALAPPDATA",
        "OS",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
    ] {
        if let Ok(value) = std::env::var(key) {
            env.insert(key.to_string(), value);
        }
    }
    env.insert("PATH".into(), {
        let mut path = format!(
            r"{root}\System32;{root}\System32\WindowsPowerShell\v1.0;{root}\System32\Wbem",
            root = system_root
        );
        for git_dir in [
            r"C:\Program Files\Git\cmd",
            r"C:\Program Files (x86)\Git\cmd",
        ] {
            if Path::new(git_dir).exists() {
                path.push(';');
                path.push_str(git_dir);
            }
        }
        if let Ok(local) = std::env::var("LOCALAPPDATA") {
            let apps = PathBuf::from(local).join("Microsoft").join("WindowsApps");
            if apps.is_dir() {
                path.push(';');
                path.push_str(&apps.to_string_lossy());
            }
        }
        path
    });
    env.insert("GIT_TERMINAL_PROMPT".into(), "0".into());
    env
}

// ---------------------------------------------------------------------------------------- paths

/// `%LOCALAPPDATA%\Alex LLM`, which is what the Python half calls the same directory.
pub fn data_root_default() -> PathBuf {
    let root = std::env::var("LOCALAPPDATA").unwrap_or_else(|_| ".".into());
    PathBuf::from(root).join("Alex LLM")
}

/// The user's own folders, from the Shell's known-folder API (a redirected Desktop is honoured).
pub fn known_folders() -> (Option<String>, Option<String>, Option<String>) {
    fn known_folder_path(id: &windows::core::GUID) -> Option<String> {
        unsafe {
            let pwstr = windows::Win32::UI::Shell::SHGetKnownFolderPath(
                id,
                windows::Win32::UI::Shell::KNOWN_FOLDER_FLAG(0),
                None,
            )
            .ok()?;
            let value = pwstr.to_string().ok();
            windows::Win32::System::Com::CoTaskMemFree(Some(pwstr.0 as *const _));
            value.filter(|item| !item.is_empty())
        }
    }
    (
        known_folder_path(&windows::Win32::UI::Shell::FOLDERID_Desktop),
        known_folder_path(&windows::Win32::UI::Shell::FOLDERID_Documents),
        known_folder_path(&windows::Win32::UI::Shell::FOLDERID_Downloads),
    )
}

#[repr(C)]
struct OsVersionInfo {
    dw_os_version_info_size: u32,
    dw_major: u32,
    dw_minor: u32,
    dw_build: u32,
    dw_platform: u32,
    sz_csd: [u16; 128],
}

#[link(name = "ntdll")]
extern "system" {
    fn RtlGetVersion(info: *mut OsVersionInfo) -> i32;
}

pub struct SystemFacts {
    pub platform: &'static str,
    pub os_version: String,
    pub cpu_logical_processors: u32,
    pub ram_total_mb: u64,
    pub ram_avail_mb: u64,
    pub disk_free_gb: u64,
}

pub fn system_info() -> SystemFacts {
    let mut version = OsVersionInfo {
        dw_os_version_info_size: std::mem::size_of::<OsVersionInfo>() as u32,
        dw_major: 0,
        dw_minor: 0,
        dw_build: 0,
        dw_platform: 0,
        sz_csd: [0; 128],
    };
    unsafe {
        let _ = RtlGetVersion(&mut version);
    }
    let mut info = windows::Win32::System::SystemInformation::SYSTEM_INFO::default();
    unsafe {
        windows::Win32::System::SystemInformation::GetNativeSystemInfo(&mut info);
    }
    let mut memory = windows::Win32::System::SystemInformation::MEMORYSTATUSEX::default();
    memory.dwLength =
        std::mem::size_of::<windows::Win32::System::SystemInformation::MEMORYSTATUSEX>() as u32;
    let _ = unsafe { windows::Win32::System::SystemInformation::GlobalMemoryStatusEx(&mut memory) };
    let mut free: u64 = 0;
    let _ = unsafe {
        windows::Win32::Storage::FileSystem::GetDiskFreeSpaceExW(
            windows::core::w!("C:\\"),
            Some(&mut free),
            None,
            None,
        )
    };
    SystemFacts {
        platform: "windows",
        os_version: format!(
            "{}.{}.{}",
            version.dw_major, version.dw_minor, version.dw_build
        ),
        cpu_logical_processors: info.dwNumberOfProcessors,
        ram_total_mb: memory.ullTotalPhys / (1024 * 1024),
        ram_avail_mb: memory.ullAvailPhys / (1024 * 1024),
        disk_free_gb: free / (1024 * 1024 * 1024),
    }
}

/// Replacing a file the user may have open: `ReplaceFileW` keeps the destination name and its
/// attributes, which a plain rename does not.
pub fn replace_file(tmp: &Path, dest: &Path) -> io::Result<()> {
    if !dest.exists() {
        return fs::rename(tmp, dest);
    }
    use std::os::windows::ffi::OsStrExt;
    use windows::core::PCWSTR;
    use windows::Win32::Storage::FileSystem::{ReplaceFileW, REPLACEFILE_WRITE_THROUGH};
    let dest_w: Vec<u16> = dest
        .as_os_str()
        .encode_wide()
        .chain(std::iter::once(0))
        .collect();
    let tmp_w: Vec<u16> = tmp
        .as_os_str()
        .encode_wide()
        .chain(std::iter::once(0))
        .collect();
    unsafe {
        ReplaceFileW(
            PCWSTR(dest_w.as_ptr()),
            PCWSTR(tmp_w.as_ptr()),
            PCWSTR::null(),
            REPLACEFILE_WRITE_THROUGH,
            None,
            None,
        )
        .map_err(|error| io::Error::other(error.to_string()))
    }
}

// -------------------------------------------------------------------------------------- secrets

fn wide(text: &str) -> Vec<u16> {
    text.encode_utf16().chain(std::iter::once(0)).collect()
}

fn cred_write(target: &str, value: &str) -> Result<(), String> {
    use windows::core::PWSTR;
    use windows::Win32::Security::Credentials::{
        CredWriteW, CREDENTIALW, CRED_PERSIST_LOCAL_MACHINE, CRED_TYPE_GENERIC,
    };

    let mut target_w = wide(target);
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
    use windows::core::PCWSTR;
    use windows::Win32::Security::Credentials::{
        CredFree, CredReadW, CREDENTIALW, CRED_TYPE_GENERIC,
    };

    let target_w = wide(target);
    unsafe {
        let mut cred: *mut CREDENTIALW = std::ptr::null_mut();
        CredReadW(PCWSTR(target_w.as_ptr()), CRED_TYPE_GENERIC, 0, &mut cred)
            .map_err(|e| e.to_string())?;
        if cred.is_null() {
            return Err("missing".into());
        }
        let blob =
            std::slice::from_raw_parts((*cred).CredentialBlob, (*cred).CredentialBlobSize as usize);
        let value = String::from_utf8_lossy(blob).into_owned();
        CredFree(cred as *const _);
        Ok(value)
    }
}

fn cred_delete(target: &str) -> Result<(), String> {
    use windows::core::PCWSTR;
    use windows::Win32::Security::Credentials::{CredDeleteW, CRED_TYPE_GENERIC};

    let target_w = wide(target);
    unsafe {
        CredDeleteW(PCWSTR(target_w.as_ptr()), CRED_TYPE_GENERIC, 0).map_err(|e| e.to_string())?;
    }
    Ok(())
}

/// The last resort before plaintext: a file only this user's DPAPI key can read.
fn dpapi_write_to(path: &Path, value: &str) -> Result<(), String> {
    use windows::Win32::Security::Cryptography::{CryptProtectData, CRYPT_INTEGER_BLOB};
    let mut input = value.as_bytes().to_vec();
    let mut in_blob = CRYPT_INTEGER_BLOB {
        cbData: input.len() as u32,
        pbData: input.as_mut_ptr(),
    };
    let mut out_blob = CRYPT_INTEGER_BLOB::default();
    unsafe {
        CryptProtectData(&mut in_blob, None, None, None, None, 0, &mut out_blob)
            .map_err(|e| e.to_string())?;
        let bytes = std::slice::from_raw_parts(out_blob.pbData, out_blob.cbData as usize);
        fs::write(path, bytes).map_err(|e| e.to_string())?;
        if !out_blob.pbData.is_null() {
            windows::Win32::Foundation::LocalFree(windows::Win32::Foundation::HLOCAL(
                out_blob.pbData as *mut c_void,
            ));
        }
    }
    Ok(())
}

fn dpapi_read_from(path: &Path) -> Option<String> {
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
                out_blob.pbData as *mut c_void,
            ));
        }
        Some(value)
    }
}

/// Credential Manager first; the DPAPI file in the data root when it refuses. A read accepts
/// either, so an entry written while the manager was unavailable is still found.
pub fn secret_read(target: &str, fallback: &Path) -> Result<String, String> {
    cred_read(target).or_else(|_| dpapi_read_from(fallback).ok_or_else(|| "missing".to_string()))
}

pub fn secret_write(target: &str, value: &str, fallback: &Path) -> Result<(), String> {
    if cred_write(target, value).is_ok() {
        return Ok(());
    }
    dpapi_write_to(fallback, value)
}

pub fn secret_delete(target: &str, fallback: &Path) -> Result<(), String> {
    let _ = cred_delete(target);
    let _ = fs::remove_file(fallback);
    Ok(())
}

/// True when the value a reader would get came from the file rather than from Credential Manager:
/// the diagnostics the UI shows name the store honestly.
pub fn secret_fallback_in_use(fallback: &Path) -> bool {
    fallback.is_file()
}

/// Windows always has a real secret store; there is no "unavailable" state.
pub fn secret_kind() -> &'static str {
    "windows_credential_manager"
}

// ------------------------------------------------------------------------------- login entry

fn registry_error(status: windows::Win32::Foundation::WIN32_ERROR) -> String {
    format!("autostart_registry_failed:{}", status.0)
}

struct RegistryKey(windows::Win32::System::Registry::HKEY);

impl Drop for RegistryKey {
    fn drop(&mut self) {
        unsafe {
            let _ = windows::Win32::System::Registry::RegCloseKey(self.0);
        }
    }
}

/// `Ok(None)` when the key does not exist at all — "nothing registered" is not a failure.
fn open_key(
    path: &str,
    access: windows::Win32::System::Registry::REG_SAM_FLAGS,
) -> Result<Option<RegistryKey>, String> {
    use windows::core::PCWSTR;
    use windows::Win32::Foundation::{ERROR_FILE_NOT_FOUND, ERROR_SUCCESS};
    use windows::Win32::System::Registry::{RegOpenKeyExW, HKEY_CURRENT_USER};
    let path = wide(path);
    let mut handle = windows::Win32::System::Registry::HKEY::default();
    let status = unsafe {
        RegOpenKeyExW(
            HKEY_CURRENT_USER,
            PCWSTR(path.as_ptr()),
            0,
            access,
            &mut handle,
        )
    };
    match status {
        ERROR_SUCCESS => Ok(Some(RegistryKey(handle))),
        ERROR_FILE_NOT_FOUND => Ok(None),
        other => Err(registry_error(other)),
    }
}

fn create_key(path: &str) -> Result<RegistryKey, String> {
    use windows::core::PCWSTR;
    use windows::Win32::Foundation::ERROR_SUCCESS;
    use windows::Win32::System::Registry::{
        RegCreateKeyExW, HKEY_CURRENT_USER, KEY_QUERY_VALUE, KEY_SET_VALUE, REG_OPTION_NON_VOLATILE,
    };
    let path = wide(path);
    let mut handle = windows::Win32::System::Registry::HKEY::default();
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
    use windows::core::PCWSTR;
    use windows::Win32::Foundation::ERROR_SUCCESS;
    use windows::Win32::System::Registry::{RegQueryValueExW, KEY_QUERY_VALUE, REG_VALUE_TYPE};
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
    if status != ERROR_SUCCESS {
        return Ok(None);
    }
    let mut buffer = vec![0u8; size as usize];
    let status = unsafe {
        RegQueryValueExW(
            key.0,
            PCWSTR(name.as_ptr()),
            None,
            None,
            Some(buffer.as_mut_ptr()),
            Some(&mut size),
        )
    };
    if status != ERROR_SUCCESS {
        return Ok(None);
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
    use windows::core::PCWSTR;
    use windows::Win32::Foundation::ERROR_SUCCESS;
    use windows::Win32::System::Registry::{RegSetValueExW, REG_SZ};
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
    use windows::core::PCWSTR;
    use windows::Win32::Foundation::{ERROR_FILE_NOT_FOUND, ERROR_SUCCESS};
    use windows::Win32::System::Registry::{RegDeleteValueW, KEY_SET_VALUE};
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
pub fn login_command(executable: &Path) -> String {
    format!("\"{}\"", executable.display())
}

#[cfg(not(test))]
fn login_key() -> &'static str {
    RUN_KEY
}

#[cfg(test)]
pub fn login_key() -> &'static str {
    TEST_KEY
}

pub fn login_read() -> Result<Option<String>, String> {
    read_value(login_key(), LOGIN_VALUE_NAME)
}

pub fn login_write(enabled: bool, executable: &Path) -> Result<Option<String>, String> {
    if !enabled {
        delete_value(login_key(), LOGIN_VALUE_NAME)?;
        return match read_value(login_key(), LOGIN_VALUE_NAME)? {
            None => Ok(None),
            Some(_) => Err("autostart_not_removed".to_string()),
        };
    }
    let command = login_command(executable);
    write_value(login_key(), LOGIN_VALUE_NAME, &command)?;
    match read_value(login_key(), LOGIN_VALUE_NAME)? {
        Some(found) if found == command => Ok(Some(command)),
        _ => Err("autostart_not_applied".to_string()),
    }
}

/// Windows registers a per-user login entry through the registry; no administrator is involved.
pub fn login_supported() -> bool {
    true
}

/// No elevation helper on this side of the contract: Windows has UAC, POSIX has sudo/polkit and the
/// product never asks for a password.
pub fn elevation_available() -> bool {
    true
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    /// One key, one test at a time: the tests share the registry they write to.
    static TEST_LOCK: Mutex<()> = Mutex::new(());

    #[test]
    fn the_registered_command_line_is_the_quoted_executable() {
        assert_eq!(
            login_command(Path::new(
                r"C:\Users\me\AppData\Local\Programs\Canalla LLM\alex-llm.exe"
            )),
            r#""C:\Users\me\AppData\Local\Programs\Canalla LLM\alex-llm.exe""#
        );
    }

    #[test]
    fn login_round_trips_through_the_registry() {
        let _guard = TEST_LOCK.lock().unwrap();
        let exe = Path::new(r"C:\Program Files\Canalla LLM\alex-llm.exe");
        login_write(false, exe).unwrap();
        assert_eq!(login_read().unwrap(), None);

        let command = login_write(true, exe).unwrap().unwrap();
        assert!(
            command.starts_with('"') && command.ends_with('"'),
            "{command}"
        );
        assert_eq!(login_read().unwrap(), Some(command));

        login_write(false, exe).unwrap();
        assert_eq!(login_read().unwrap(), None);
        // An uninstall on a machine where nothing was ever registered must not fail.
        login_write(false, exe).unwrap();
    }

    #[test]
    fn writing_twice_keeps_exactly_one_value() {
        let _guard = TEST_LOCK.lock().unwrap();
        let exe = Path::new(r"C:\Program Files\Canalla LLM\alex-llm.exe");
        let first = login_write(true, exe).unwrap();
        let second = login_write(true, exe).unwrap();
        assert_eq!(first, second);
        assert_eq!(login_read().unwrap(), second);
        login_write(false, exe).unwrap();
    }
}
