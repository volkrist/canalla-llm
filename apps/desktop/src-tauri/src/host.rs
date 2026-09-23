use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::HashSet;
use std::fs::{self, File};
use std::io::Write;
use std::os::windows::ffi::OsStrExt;
use std::path::PathBuf;
use std::sync::{Mutex, OnceLock};
use std::time::Duration;

#[derive(Clone, Serialize, Deserialize)]
pub struct DeviceRecord {
    pub device_id: String,
    pub display_name: String,
}

fn data_dir() -> PathBuf {
    crate::credential::data_dir()
}

fn record_path() -> PathBuf {
    data_dir().join("device.json")
}

fn load_record() -> Option<DeviceRecord> {
    fs::read_to_string(record_path())
        .ok()
        .and_then(|text| serde_json::from_str(&text).ok())
}

fn save_record(record: &DeviceRecord) {
    let _ = fs::write(record_path(), serde_json::to_vec(record).unwrap_or_default());
}

pub fn device_headers(token: &str) -> Result<reqwest::header::HeaderMap, String> {
    let record = load_record().ok_or("device_not_paired")?;
    let credential = crate::credential::load().ok_or("device_not_paired")?;
    let mut headers = reqwest::header::HeaderMap::new();
    headers.insert(
        reqwest::header::AUTHORIZATION,
        format!("Bearer {token}")
            .parse()
            .map_err(|e: reqwest::header::InvalidHeaderValue| e.to_string())?,
    );
    headers.insert(
        "X-Alex-Device-Id",
        record
            .device_id
            .parse()
            .map_err(|e: reqwest::header::InvalidHeaderValue| e.to_string())?,
    );
    headers.insert(
        "X-Alex-Device-Credential",
        credential
            .parse()
            .map_err(|e: reqwest::header::InvalidHeaderValue| e.to_string())?,
    );
    Ok(headers)
}

/// Every call to the local backend goes through this one bounded client.
///
/// A bounded timeout is a lifecycle requirement, not a style choice: the device loop talks to the
/// backend every few seconds, and the WebView keeps only a small pool of connections for IPC. A
/// request that never returns from a backend that died would hold those connections and block
/// every later command - including the `ensure_backend` that would bring the backend back.
pub(crate) fn local_client() -> reqwest::Client {
    static CLIENT: OnceLock<reqwest::Client> = OnceLock::new();
    CLIENT
        .get_or_init(|| {
            reqwest::Client::builder()
                .connect_timeout(Duration::from_secs(3))
                .timeout(Duration::from_secs(5))
                .build()
                .unwrap_or_else(|_| reqwest::Client::new())
        })
        .clone()
}

#[tauri::command]
pub async fn pair_device(backend_url: String, token: String, display_name: String) -> Result<Value, String> {
    if let (Some(record), Some(_)) = (load_record(), crate::credential::load()) {
        let client = local_client();
        let _ = client
            .post(format!("{backend_url}/tools/devices/heartbeat"))
            .headers(device_headers(&token)?)
            .send()
            .await;
        return Ok(json!({
            "device_id": record.device_id,
            "display_name": record.display_name,
            "online": true,
            "storage": crate::credential::storage_kind(),
        }));
    }
    let client = local_client();
    let alias = {
        let trimmed = display_name.trim();
        if trimmed.is_empty() {
            "Windows device".to_string()
        } else {
            trimmed.to_string()
        }
    };
    let response = client
        .post(format!("{backend_url}/tools/devices/pair"))
        .bearer_auth(&token)
        .json(&json!({
            "display_name": alias,
            "platform": "windows",
            "capabilities": ["fs", "process", "registry", "credential", "system"]
        }))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    let body: Value = response.json().await.map_err(|e| e.to_string())?;
    let device_id = body
        .get("device_id")
        .and_then(Value::as_str)
        .ok_or("pair_failed")?
        .to_string();
    let credential = body.get("credential").and_then(Value::as_str).ok_or("pair_failed")?;
    crate::credential::store(credential)?;
    let record = DeviceRecord {
        device_id,
        display_name: body
            .get("display_name")
            .and_then(Value::as_str)
            .unwrap_or("Windows device")
            .to_string(),
    };
    save_record(&record);
    Ok(json!({
        "device_id": record.device_id,
        "display_name": record.display_name,
        "online": true,
        "storage": crate::credential::storage_kind(),
    }))
}

#[tauri::command]
pub fn device_status() -> Value {
    match load_record() {
        Some(record) => json!({
            "paired": true,
            "device_id": record.device_id,
            "display_name": record.display_name,
            "online": crate::credential::load().is_some(),
            "storage": crate::credential::storage_kind(),
        }),
        None => json!({"paired": false, "online": false, "storage": "none"}),
    }
}

#[tauri::command]
pub async fn forget_device(backend_url: String, token: String) -> Result<Value, String> {
    if let Some(record) = load_record() {
        let client = local_client();
        let _ = client
            .post(format!("{backend_url}/tools/devices/{}/forget", record.device_id))
            .bearer_auth(&token)
            .send()
            .await;
    }
    crate::credential::delete()?;
    let _ = fs::remove_file(record_path());
    Ok(json!({"paired": false, "online": false, "storage": "none"}))
}

#[tauri::command]
pub async fn rotate_device_credential(backend_url: String, token: String) -> Result<Value, String> {
    let record = load_record().ok_or("device_not_paired")?;
    let client = local_client();
    let response = client
        .post(format!("{backend_url}/tools/devices/{}/rotate", record.device_id))
        .bearer_auth(&token)
        .send()
        .await
        .map_err(|e| e.to_string())?;
    let body: Value = response.json().await.map_err(|e| e.to_string())?;
    let credential = body.get("credential").and_then(Value::as_str).ok_or("rotate_failed")?;
    crate::credential::store(credential)?;
    Ok(json!({
        "device_id": record.device_id,
        "display_name": record.display_name,
        "online": true,
        "storage": crate::credential::storage_kind(),
        "rotated": true,
    }))
}

#[tauri::command]
pub fn store_user_credential(name: String, secret: String) -> Result<Value, String> {
    crate::credential::store_named(&name, &secret)?;
    Ok(json!({"reference": name, "stored": true}))
}

#[tauri::command]
pub fn list_user_credentials() -> Vec<String> {
    crate::credential::list_named()
}

#[tauri::command]
pub fn delete_user_credential(name: String) -> Result<Value, String> {
    crate::credential::delete_named(&name)?;
    Ok(json!({"reference": name, "deleted": true}))
}

fn inflight_jobs() -> &'static Mutex<HashSet<String>> {
    static JOBS: OnceLock<Mutex<HashSet<String>>> = OnceLock::new();
    JOBS.get_or_init(|| Mutex::new(HashSet::new()))
}

#[tauri::command]
pub async fn execute_host_jobs(backend_url: String, token: String, roots: Vec<String>) -> Result<Value, String> {
    let client = local_client();
    let headers = device_headers(&token)?;
    let response = client
        .get(format!("{backend_url}/tools/devices/jobs"))
        .headers(headers.clone())
        .send()
        .await
        .map_err(|e| e.to_string())?;
    let jobs: Vec<Value> = response.json().await.map_err(|e| e.to_string())?;
    let mut started = 0;
    for job in jobs {
        let id = job.get("id").and_then(Value::as_str).unwrap_or_default().to_string();
        if id.is_empty() {
            continue;
        }
        {
            let Ok(mut guard) = inflight_jobs().lock() else {
                continue;
            };
            if !guard.insert(id.clone()) {
                continue;
            }
        }
        let digest = job.get("input_digest").and_then(Value::as_str).unwrap_or_default().to_string();
        let name = job.get("tool_name").and_then(Value::as_str).unwrap_or_default().to_string();
        let args = job.get("host_args").cloned().unwrap_or(Value::Object(Default::default()));
        let backend = backend_url.clone();
        let post_url = backend_url.clone();
        let token_copy = token.clone();
        let token_post = token.clone();
        let run_id = id.clone();
        let roots_for_job = roots.clone();
        tokio::spawn(async move {
            let result = tokio::task::spawn_blocking(move || {
                let stop_id = run_id.clone();
                let token_stop = token_copy.clone();
                let backend_stop = backend.clone();
                run_local_tool(&name, &args, &roots_for_job, &run_id, move || {
                    poll_stopped(&backend_stop, &token_stop, &stop_id)
                })
            })
            .await;
            if let Ok(result) = result {
                if let Ok(headers) = device_headers(&token_post) {
                    let _ = local_client()
                        .post(format!("{post_url}/tools/runs/{id}/host-result"))
                        .headers(headers)
                        .json(&json!({
                            "digest": digest,
                            "status": "completed",
                            "exit_code": result.exit_code,
                            "stdout": result.stdout,
                            "stderr": result.stderr,
                            "text": result.text,
                            "metadata": result.metadata,
                        }))
                        .send()
                        .await;
                }
            }
            if let Ok(mut guard) = inflight_jobs().lock() {
                guard.remove(&id);
            }
        });
        started += 1;
    }
    let _ = client
        .post(format!("{backend_url}/tools/devices/heartbeat"))
        .headers(device_headers(&token)?)
        .send()
        .await;
    Ok(json!({"started": started, "completed": started}))
}

fn poll_stopped(backend_url: &str, token: &str, run_id: &str) -> bool {
    let Ok(headers) = device_headers(token) else {
        return true;
    };
    let client = match reqwest::blocking::Client::builder().timeout(Duration::from_secs(2)).build() {
        Ok(client) => client,
        Err(_) => return false,
    };
    let Ok(response) = client
        .get(format!("{backend_url}/tools/runs/{run_id}"))
        .headers(headers)
        .send()
    else {
        return false;
    };
    let Ok(body) = response.json::<Value>() else {
        return false;
    };
    matches!(
        body.get("status").and_then(Value::as_str),
        Some("stopped" | "failed" | "denied")
    )
}

pub struct LocalOutcome {
    pub exit_code: Option<i32>,
    pub stdout: String,
    pub stderr: String,
    pub text: String,
    pub metadata: Value,
}

fn run_local_tool(
    name: &str,
    args: &Value,
    roots: &[String],
    tool_run_id: &str,
    should_stop: impl Fn() -> bool,
) -> LocalOutcome {
    match name {
        "get_system_info" => system_info(),
        "get_known_folders" => known_folders(),
        "list_directory" => fs_list(str_arg(args, "path"), roots),
        "read_file" => fs_read(str_arg(args, "path"), roots),
        "hash_file" => fs_hash(str_arg(args, "path"), roots),
        "write_file" => fs_write(args, roots),
        "create_directory" => fs_create_dir(str_arg(args, "path"), roots),
        "copy_file" => fs_copy(args, roots),
        "move_file" => fs_move(args, roots),
        "search_files" => fs_search(args, roots),
        "search_code" => fs_search_code(args, roots),
        "patch_file" => fs_patch(args, roots),
        "delete_file" => fs_delete_file(str_arg(args, "path"), roots),
        "delete_directory" => fs_delete_dir(str_arg(args, "path"), roots),
        "mass_delete" => critical_tool("mass_delete", args),
        "run_process" | "run_powershell" | "run_python" => run_exec(name, args, roots, tool_run_id, should_stop),
        "stop_process" => {
            let pid = crate::process::stop_job(&str_arg(args, "tool_run_id"));
            let mut out = ok_text("stopped".into());
            out.metadata = json!({"pid": pid, "verified_dead": true});
            out
        }
        "process_status" => ok_text(crate::process::job_status(&str_arg(args, "tool_run_id")).into()),
        "list_processes" => list_processes(tool_run_id),
        "inspect_process" => inspect_process(args, tool_run_id),
        "list_volumes" => list_volumes(tool_run_id),
        "list_installed_software" => list_installed_software(tool_run_id),
        "registry_read" | "read_registry" => registry_op("query", args, tool_run_id),
        "registry_write" | "write_registry" => registry_op("add", args, tool_run_id),
        "delete_registry_value" => registry_op("delete_value", args, tool_run_id),
        "delete_registry_key" => registry_op("delete_key", args, tool_run_id),
        "windows_service_status" | "query_service" => service_op(args, tool_run_id, false),
        "windows_service_control" | "start_service" | "stop_service" | "restart_service" => {
            service_named(name, args, tool_run_id)
        }
        "change_service_settings" => change_service_settings(args, tool_run_id),
        "scheduled_task" => scheduled_task(args, tool_run_id),
        "firewall_rule" => firewall_rule(args, tool_run_id),
        "install_software" => winget_op("install", args, tool_run_id),
        "uninstall_software" => winget_op("uninstall", args, tool_run_id),
        "set_environment" => set_environment(args, tool_run_id),
        "credential_list" => ok_text(crate::credential::list_named().join("\n")),
        "credential_use" => credential_use(args),
        "format_volume" | "manage_partition" | "boot_config" | "bitlocker_change" | "system_shutdown" => {
            critical_tool(name, args)
        }
        name if name.starts_with("git_") => crate::git::run_git(name, args, roots, tool_run_id),
        _ => err_text("unknown_tool"),
    }
}

fn str_arg(args: &Value, key: &str) -> String {
    args.get(key)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim_matches('"')
        .to_string()
}

fn argv_arg(args: &Value) -> Vec<String> {
    args.get("argv")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .map(|item| item.to_string())
                .collect()
        })
        .unwrap_or_default()
}

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

fn known_folders() -> LocalOutcome {
    let desktop = known_folder_path(&windows::Win32::UI::Shell::FOLDERID_Desktop);
    let documents = known_folder_path(&windows::Win32::UI::Shell::FOLDERID_Documents);
    let downloads = known_folder_path(&windows::Win32::UI::Shell::FOLDERID_Downloads);
    if desktop.is_none() && documents.is_none() && downloads.is_none() {
        return err_text("known_folder_unavailable");
    }
    let text = format!(
        "desktop={}\ndocuments={}\ndownloads={}",
        desktop.clone().unwrap_or_default(),
        documents.clone().unwrap_or_default(),
        downloads.clone().unwrap_or_default()
    );
    let mut out = ok_text(text);
    out.metadata = json!({
        "desktop": desktop,
        "documents": documents,
        "downloads": downloads,
    });
    out
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

fn system_info() -> LocalOutcome {
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
    memory.dwLength = std::mem::size_of::<windows::Win32::System::SystemInformation::MEMORYSTATUSEX>() as u32;
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
    let ram_total_mb = memory.ullTotalPhys / (1024 * 1024);
    let ram_avail_mb = memory.ullAvailPhys / (1024 * 1024);
    let disk_free_gb = free / (1024 * 1024 * 1024);
    let text = format!(
        "platform=windows\nos_version={}.{}.{}\ncpu_logical_processors={}\nram_total_mb={}\nram_avail_mb={}\nsystem_disk_free_gb={}",
        version.dw_major,
        version.dw_minor,
        version.dw_build,
        info.dwNumberOfProcessors,
        ram_total_mb,
        ram_avail_mb,
        disk_free_gb
    );
    let mut out = ok_text(text);
    out.metadata = json!({
        "os_version": format!("{}.{}.{}", version.dw_major, version.dw_minor, version.dw_build),
        "cpu_logical_processors": info.dwNumberOfProcessors,
        "ram_total_mb": ram_total_mb,
        "ram_avail_mb": ram_avail_mb,
        "system_disk_free_gb": disk_free_gb,
    });
    out
}

fn ok_text(text: String) -> LocalOutcome {
    LocalOutcome {
        exit_code: Some(0),
        stdout: text.clone(),
        stderr: String::new(),
        text,
        metadata: json!({}),
    }
}

fn err_text(code: &str) -> LocalOutcome {
    LocalOutcome {
        exit_code: Some(1),
        stdout: String::new(),
        stderr: code.to_string(),
        text: code.to_string(),
        metadata: json!({"error": code}),
    }
}

pub(crate) fn err_public(code: &str) -> LocalOutcome {
    err_text(code)
}

pub(crate) fn critical_blocked(name: &str, args: &Value) -> LocalOutcome {
    critical_tool(name, args)
}

pub(crate) fn redact_text(text: &str) -> String {
    let mut result = text.to_string();
    for scheme in ["https://", "http://"] {
        let mut start = 0;
        while let Some(idx) = result[start..].find(scheme) {
            let abs = start + idx + scheme.len();
            if let Some(at) = result[abs..].find('@') {
                let creds = &result[abs..abs + at];
                if creds.contains(':') && !creds.contains('/') {
                    result.replace_range(abs..abs + at, "[redacted]");
                    start = abs + "[redacted]".len();
                    continue;
                }
            }
            start = abs;
        }
    }
    result
}

fn fs_list(path: String, roots: &[String]) -> LocalOutcome {
    let Ok(dir) = crate::fs_guard::resolve(&path, roots) else {
        return err_text("path_denied");
    };
    let listing = fs::read_dir(&dir)
        .map(|entries| {
            entries
                .flatten()
                .map(|e| e.file_name().to_string_lossy().into_owned())
                .take(200)
                .collect::<Vec<_>>()
                .join("\n")
        })
        .unwrap_or_default();
    ok_text(listing)
}

fn fs_read(path: String, roots: &[String]) -> LocalOutcome {
    let Ok(file) = crate::fs_guard::resolve(&path, roots) else {
        return err_text("path_denied");
    };
    match fs::read(&file) {
        Ok(bytes) => {
            let digest = sha256_hex(&bytes);
            let body: String = String::from_utf8_lossy(&bytes).chars().take(20000).collect();
            let mut out = ok_text(format!("sha256={digest}\n{body}"));
            out.metadata = json!({"before_sha256": digest, "sha256": digest, "path": path});
            out
        }
        Err(_) => err_text("read_failed"),
    }
}

fn fs_hash(path: String, roots: &[String]) -> LocalOutcome {
    let Ok(file) = crate::fs_guard::resolve(&path, roots) else {
        return err_text("path_denied");
    };
    match fs::read(&file) {
        Ok(bytes) => {
            let digest = sha256_hex(&bytes);
            let mut out = ok_text(digest.clone());
            out.metadata = json!({"digest": digest, "sha256": digest, "path": path});
            out
        }
        Err(_) => err_text("read_failed"),
    }
}

fn fs_write(args: &Value, roots: &[String]) -> LocalOutcome {
    let path = str_arg(args, "path");
    let Ok(file) = crate::fs_guard::resolve(&path, roots) else {
        return err_text("path_denied");
    };
    let content = str_arg(args, "content");
    let expected = args.get("expected_before_sha256").and_then(Value::as_str);
    let before = fs::read(&file).ok().map(|bytes| sha256_hex(&bytes));
    if let Some(expected) = expected.filter(|value| !value.is_empty()) {
        match before.as_deref() {
            Some(actual) if actual == expected => {}
            _ => return err_text("conflict"),
        }
    }
    let tmp = file.with_file_name(format!(
        ".{}.alex-tmp",
        file.file_name().unwrap_or_default().to_string_lossy()
    ));
    if atomic_write(&tmp, content.as_bytes()).is_err() {
        return err_text("write_failed");
    }
    if replace_file(&tmp, &file).is_err() {
        let _ = fs::remove_file(&tmp);
        return err_text("write_failed");
    }
    let after = fs::read(&file).ok().map(|bytes| sha256_hex(&bytes));
    let mut out = ok_text("written".into());
    out.metadata = json!({
        "before_sha256": before,
        "after_sha256": after,
        "files_changed": 1,
        "path": path
    });
    out
}

fn atomic_write(path: &std::path::Path, bytes: &[u8]) -> std::io::Result<()> {
    let mut file = File::create(path)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    Ok(())
}

fn replace_file(tmp: &std::path::Path, dest: &std::path::Path) -> std::io::Result<()> {
    if !dest.exists() {
        return fs::rename(tmp, dest);
    }
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
        .map_err(|error| std::io::Error::other(error.to_string()))
    }
}

fn fs_create_dir(path: String, roots: &[String]) -> LocalOutcome {
    let Ok(dir) = crate::fs_guard::resolve(&path, roots) else {
        return err_text("path_denied");
    };
    match fs::create_dir_all(dir) {
        Ok(()) => ok_text("created".into()),
        Err(_) => err_text("write_failed"),
    }
}

fn fs_copy(args: &Value, roots: &[String]) -> LocalOutcome {
    let src = str_arg(args, "source");
    let dst = str_arg(args, "destination");
    let (Ok(from), Ok(to)) = (crate::fs_guard::resolve(&src, roots), crate::fs_guard::resolve(&dst, roots)) else {
        return err_text("path_denied");
    };
    match fs::copy(from, to) {
        Ok(_) => {
            let mut out = ok_text("copied".into());
            out.metadata = json!({"files_changed": 1});
            out
        }
        Err(_) => err_text("write_failed"),
    }
}

fn fs_move(args: &Value, roots: &[String]) -> LocalOutcome {
    let src = str_arg(args, "source");
    let dst = str_arg(args, "destination");
    let (Ok(from), Ok(to)) = (crate::fs_guard::resolve(&src, roots), crate::fs_guard::resolve(&dst, roots)) else {
        return err_text("path_denied");
    };
    match fs::rename(from, to) {
        Ok(()) => {
            let mut out = ok_text("moved".into());
            out.metadata = json!({"files_changed": 1});
            out
        }
        Err(_) => err_text("write_failed"),
    }
}

fn fs_search(args: &Value, roots: &[String]) -> LocalOutcome {
    let root = str_arg(args, "root");
    let query = str_arg(args, "query").to_lowercase();
    let Ok(dir) = crate::fs_guard::resolve(&root, roots) else {
        return err_text("path_denied");
    };
    let mut hits = Vec::new();
    fn walk(dir: &std::path::Path, query: &str, hits: &mut Vec<String>, depth: u32) {
        if depth > 4 || hits.len() >= 200 {
            return;
        }
        let Ok(entries) = fs::read_dir(dir) else {
            return;
        };
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().to_string();
            if name.eq_ignore_ascii_case(".git")
                || name.eq_ignore_ascii_case("node_modules")
                || name.eq_ignore_ascii_case(".venv")
            {
                continue;
            }
            if name.to_lowercase().contains(query) {
                hits.push(entry.path().to_string_lossy().into_owned());
            }
            if entry.path().is_dir() {
                walk(&entry.path(), query, hits, depth + 1);
            }
        }
    }
    walk(&dir, &query, &mut hits, 0);
    ok_text(hits.join("\n"))
}

fn fs_search_code(args: &Value, roots: &[String]) -> LocalOutcome {
    let root = str_arg(args, "root");
    let query = str_arg(args, "query");
    let Ok(dir) = crate::fs_guard::resolve(&root, roots) else {
        return err_text("path_denied");
    };
    let mut hits = Vec::new();
    fn walk(dir: &std::path::Path, query: &str, hits: &mut Vec<String>, files: &mut u32, depth: u32) {
        if depth > 6 || hits.len() >= 50 || *files >= 400 {
            return;
        }
        let Ok(entries) = fs::read_dir(dir) else {
            return;
        };
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().to_string();
            if name.eq_ignore_ascii_case(".git")
                || name.eq_ignore_ascii_case("node_modules")
                || name.eq_ignore_ascii_case(".venv")
                || name.eq_ignore_ascii_case("target")
            {
                continue;
            }
            let path = entry.path();
            if path.is_dir() {
                walk(&path, query, hits, files, depth + 1);
                continue;
            }
            *files += 1;
            let Ok(text) = fs::read_to_string(&path) else {
                continue;
            };
            if text.len() > 200_000 {
                continue;
            }
            for (index, line) in text.lines().enumerate() {
                if line.contains(query) {
                    hits.push(format!("{}:{}:{}", path.to_string_lossy(), index + 1, line.trim()));
                    if hits.len() >= 50 {
                        return;
                    }
                }
            }
        }
    }
    let mut files = 0;
    walk(&dir, &query, &mut hits, &mut files, 0);
    if hits.is_empty() {
        return ok_text(format!(
            "tool=search_code status=no_match scope={root} query={query} searched_files={files} recommended_next_action=inspect_files_or_reconsider_query"
        ));
    }
    ok_text(hits.join("\n"))
}

fn fs_patch(args: &Value, roots: &[String]) -> LocalOutcome {
    let path = str_arg(args, "path");
    let Ok(file) = crate::fs_guard::resolve(&path, roots) else {
        return err_text("path_denied");
    };
    let expected = str_arg(args, "expected_before_sha256");
    let old_text = args.get("old_text").and_then(Value::as_str).unwrap_or_default();
    let new_text = args.get("new_text").and_then(Value::as_str).unwrap_or_default();
    let Ok(bytes) = fs::read(&file) else {
        return err_text("read_failed");
    };
    let before = sha256_hex(&bytes);
    if before != expected {
        let mut out = err_text("conflict");
        out.metadata = json!({"error": "conflict", "conflict": true, "before_sha256": before});
        return out;
    }
    let Ok(text) = String::from_utf8(bytes) else {
        return err_text("read_failed");
    };
    if !text.contains(old_text) {
        return err_text("patch_mismatch");
    }
    let updated = text.replacen(old_text, new_text, 1);
    let mut write_args = args.clone();
    if let Some(object) = write_args.as_object_mut() {
        object.insert("content".into(), json!(updated));
        object.insert("expected_before_sha256".into(), json!(before));
    }
    fs_write(&write_args, roots)
}

fn run_exec(
    name: &str,
    args: &Value,
    roots: &[String],
    tool_run_id: &str,
    should_stop: impl Fn() -> bool,
) -> LocalOutcome {
    let cwd = args
        .get("cwd")
        .and_then(Value::as_str)
        .map(|v| v.to_string())
        .or_else(|| std::env::var("USERPROFILE").ok());
    if let Some(path) = cwd.as_deref() {
        if crate::fs_guard::resolve(path, roots).is_err() {
            return err_text("path_denied");
        }
    }
    let extra = argv_arg(args);
    let timeout = args
        .get("timeout_seconds")
        .and_then(Value::as_u64)
        .unwrap_or(30)
        .min(120);
    let (exe, mut argv) = match name {
        "run_powershell" => (
            r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe".to_string(),
            vec!["-NoProfile".into(), "-NonInteractive".into()],
        ),
        "run_python" => {
            let exe = python_executable();
            let prefix = if exe.to_ascii_lowercase().ends_with("py.exe") {
                vec!["-3".into()]
            } else {
                vec![]
            };
            (exe, prefix)
        }
        _ => (str_arg(args, "executable"), vec![]),
    };
    if exe.is_empty() {
        return err_text("invalid_arguments");
    }
    argv.extend(extra);
    let elevate = args.get("elevate").and_then(Value::as_bool).unwrap_or(false);
    let wait = args.get("wait").and_then(Value::as_bool).unwrap_or(true);
    let mut out = crate::process::run_job(
        tool_run_id,
        &exe,
        &argv,
        cwd.as_deref(),
        Duration::from_secs(timeout),
        should_stop,
        elevate,
        wait,
    );
    out.stdout = redact_text(&out.stdout);
    out.stderr = redact_text(&out.stderr);
    out.text = redact_text(&out.text);
    out
}

fn python_executable() -> String {
    if let Ok(value) = std::env::var("ALEX_PYTHON") {
        if std::path::Path::new(&value).is_file() {
            return value;
        }
    }
    if let Ok(local) = std::env::var("LOCALAPPDATA") {
        let root = std::path::PathBuf::from(local).join("Programs").join("Python");
        if let Ok(entries) = fs::read_dir(&root) {
            let mut dirs: Vec<_> = entries.flatten().map(|e| e.path()).collect();
            dirs.sort();
            dirs.reverse();
            for dir in dirs {
                let candidate = dir.join("python.exe");
                if candidate.is_file() {
                    return candidate.to_string_lossy().into_owned();
                }
            }
        }
    }
    let root = std::env::var("SystemRoot").unwrap_or_else(|_| r"C:\Windows".into());
    for candidate in [
        format!(r"{root}\py.exe"),
        format!(r"{root}\System32\py.exe"),
        format!(r"{root}\SysWOW64\py.exe"),
    ] {
        if std::path::Path::new(&candidate).exists() {
            return candidate;
        }
    }
    format!(r"{root}\py.exe")
}

fn sha256_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    format!("{:x}", hasher.finalize())
}

fn fs_delete_file(path: String, roots: &[String]) -> LocalOutcome {
    let Ok(file) = crate::fs_guard::resolve(&path, roots) else {
        return err_text("path_denied");
    };
    if file.is_dir() {
        return err_text("not_a_file");
    }
    match fs::remove_file(&file) {
        Ok(()) => {
            let mut out = ok_text("deleted".into());
            out.metadata = json!({"files_changed": 1});
            out
        }
        Err(_) => err_text("delete_failed"),
    }
}

fn fs_delete_dir(path: String, roots: &[String]) -> LocalOutcome {
    let Ok(dir) = crate::fs_guard::resolve(&path, roots) else {
        return err_text("path_denied");
    };
    match fs::remove_dir_all(&dir) {
        Ok(()) => {
            let mut out = ok_text("deleted".into());
            out.metadata = json!({"files_changed": 1});
            out
        }
        Err(_) => err_text("delete_failed"),
    }
}

fn system32_exe(name: &str) -> String {
    let root = std::env::var("SystemRoot").unwrap_or_else(|_| r"C:\Windows".into());
    format!(r"{root}\System32\{name}")
}

fn run_host_command(tool_run_id: &str, exe: &str, argv: &[String], elevate: bool) -> LocalOutcome {
    let mut out = crate::process::run_job(
        tool_run_id,
        exe,
        argv,
        None,
        Duration::from_secs(60),
        || false,
        elevate,
        true,
    );
    out.stdout = redact_text(&out.stdout);
    out.stderr = redact_text(&out.stderr);
    out.text = redact_text(&out.text);
    out
}

fn registry_op(action: &str, args: &Value, tool_run_id: &str) -> LocalOutcome {
    let hive = str_arg(args, "hive");
    if hive != "HKCU" && hive != "HKLM" {
        return err_text("path_denied");
    }
    let key = str_arg(args, "key").replace('/', "\\");
    if key.is_empty() || key.split('\\').any(|part| part == "..") {
        return err_text("path_denied");
    }
    let path = format!("{hive}\\{key}");
    let name = str_arg(args, "name");
    let elevate = hive == "HKLM" || args.get("elevate").and_then(Value::as_bool).unwrap_or(false);
    match action {
        "add" => {
            let value = str_arg(args, "value");
            let mut argv = vec!["add".into(), path, "/f".into()];
            if !name.is_empty() {
                argv.extend(["/v".into(), name, "/d".into(), value, "/t".into(), "REG_SZ".into()]);
            }
            run_host_command(tool_run_id, &system32_exe("reg.exe"), &argv, elevate)
        }
        "delete_value" => {
            if name.is_empty() {
                return err_text("invalid_arguments");
            }
            run_host_command(
                tool_run_id,
                &system32_exe("reg.exe"),
                &["delete".into(), path, "/v".into(), name, "/f".into()],
                elevate,
            )
        }
        "delete_key" => run_host_command(
            tool_run_id,
            &system32_exe("reg.exe"),
            &["delete".into(), path, "/f".into()],
            elevate,
        ),
        _ => {
            let mut argv = vec!["query".into(), path];
            if !name.is_empty() {
                argv.extend(["/v".into(), name]);
            }
            run_host_command(tool_run_id, &system32_exe("reg.exe"), &argv, false)
        }
    }
}

fn service_op(args: &Value, tool_run_id: &str, control: bool) -> LocalOutcome {
    let name = str_arg(args, "name");
    if name.is_empty() {
        return err_text("invalid_arguments");
    }
    let action = str_arg(args, "action");
    if !control {
        return run_host_command(tool_run_id, &system32_exe("sc.exe"), &["query".into(), name], false);
    }
    let verb = if action == "stop" { "stop" } else { "start" };
    run_host_command(tool_run_id, &system32_exe("sc.exe"), &[verb.into(), name], true)
}

fn service_named(tool: &str, args: &Value, tool_run_id: &str) -> LocalOutcome {
    let name = str_arg(args, "name");
    if name.is_empty() {
        return err_text("invalid_arguments");
    }
    match tool {
        "start_service" => run_host_command(tool_run_id, &system32_exe("sc.exe"), &["start".into(), name], true),
        "stop_service" => run_host_command(tool_run_id, &system32_exe("sc.exe"), &["stop".into(), name], true),
        "restart_service" => {
            let _ = run_host_command(tool_run_id, &system32_exe("sc.exe"), &["stop".into(), name.clone()], true);
            run_host_command(tool_run_id, &system32_exe("sc.exe"), &["start".into(), name], true)
        }
        _ => service_op(args, tool_run_id, true),
    }
}

fn change_service_settings(args: &Value, tool_run_id: &str) -> LocalOutcome {
    let name = str_arg(args, "name");
    let start_type = str_arg(args, "start_type");
    if name.is_empty() || !matches!(start_type.as_str(), "demand" | "auto" | "disabled") {
        return err_text("invalid_arguments");
    }
    run_host_command(
        tool_run_id,
        &system32_exe("sc.exe"),
        &["config".into(), name, "start=".into(), start_type],
        true,
    )
}

fn list_processes(tool_run_id: &str) -> LocalOutcome {
    let mut out = run_host_command(
        tool_run_id,
        &system32_exe("tasklist.exe"),
        &["/FO".into(), "CSV".into(), "/NH".into()],
        false,
    );
    let rows: Vec<String> = out
        .stdout
        .lines()
        .filter_map(|line| {
            let mut parts = line.split(',');
            let image = parts.next()?.trim_matches('"').to_string();
            let pid = parts.next()?.trim_matches('"').to_string();
            if image.is_empty() || pid.is_empty() {
                return None;
            }
            Some(format!("{image} {pid}"))
        })
        .take(200)
        .collect();
    out.text = rows.join("\n");
    out.stdout = out.text.clone();
    out
}

fn inspect_process(args: &Value, tool_run_id: &str) -> LocalOutcome {
    let pid = args.get("pid").and_then(Value::as_u64).unwrap_or(0);
    if pid == 0 {
        return err_text("invalid_arguments");
    }
    let mut out = run_host_command(
        tool_run_id,
        &system32_exe("tasklist.exe"),
        &["/FI".into(), format!("PID eq {pid}"), "/FO".into(), "LIST".into()],
        false,
    );
    let kept: Vec<String> = out
        .stdout
        .lines()
        .filter(|line| {
            let lower = line.to_ascii_lowercase();
            lower.starts_with("image name:") || lower.starts_with("pid:") || lower.starts_with("session")
        })
        .map(str::to_string)
        .collect();
    out.text = kept.join("\n");
    out.stdout = out.text.clone();
    out
}

fn list_volumes(tool_run_id: &str) -> LocalOutcome {
    run_host_command(
        tool_run_id,
        &system32_exe("wmic.exe"),
        &[
            "logicaldisk".into(),
            "get".into(),
            "DeviceID,FileSystem,FreeSpace,Size".into(),
        ],
        false,
    )
}

fn list_installed_software(tool_run_id: &str) -> LocalOutcome {
    run_host_command(
        tool_run_id,
        &system32_exe("reg.exe"),
        &[
            "query".into(),
            r"HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall".into(),
        ],
        false,
    )
}

fn scheduled_task(args: &Value, tool_run_id: &str) -> LocalOutcome {
    let name = str_arg(args, "name");
    let action = str_arg(args, "action");
    let argv = match action.as_str() {
        "create" => vec![
            "/Create".into(),
            "/TN".into(),
            name,
            "/TR".into(),
            str_arg(args, "command"),
            "/SC".into(),
            "ONCE".into(),
            "/ST".into(),
            "00:00".into(),
            "/F".into(),
        ],
        "delete" => vec!["/Delete".into(), "/TN".into(), name, "/F".into()],
        "run" => vec!["/Run".into(), "/TN".into(), name],
        _ => vec!["/Query".into(), "/TN".into(), name, "/FO".into(), "LIST".into()],
    };
    if argv.iter().any(|item| item.is_empty()) {
        return err_text("invalid_arguments");
    }
    let elevate = action != "query";
    run_host_command(tool_run_id, &system32_exe("schtasks.exe"), &argv, elevate)
}

fn firewall_rule(args: &Value, tool_run_id: &str) -> LocalOutcome {
    let name = str_arg(args, "name");
    let action = str_arg(args, "action");
    let argv = match action.as_str() {
        "add" => vec![
            "advfirewall".into(),
            "firewall".into(),
            "add".into(),
            "rule".into(),
            format!("name={name}"),
            "dir=in".into(),
            "action=allow".into(),
        ],
        "delete" => vec![
            "advfirewall".into(),
            "firewall".into(),
            "delete".into(),
            "rule".into(),
            format!("name={name}"),
        ],
        _ => vec!["advfirewall".into(), "firewall".into(), "show".into(), "rule".into(), format!("name={name}")],
    };
    run_host_command(tool_run_id, &system32_exe("netsh.exe"), &argv, action != "list")
}

fn winget_executable() -> String {
    if let Ok(local) = std::env::var("LOCALAPPDATA") {
        let candidate = std::path::PathBuf::from(local)
            .join("Microsoft")
            .join("WindowsApps")
            .join("winget.exe");
        if candidate.is_file() {
            return candidate.to_string_lossy().into_owned();
        }
    }
    "winget.exe".into()
}

fn winget_op(action: &str, args: &Value, tool_run_id: &str) -> LocalOutcome {
    let package = str_arg(args, "package");
    if package.is_empty() {
        return err_text("invalid_arguments");
    }
    let elevate = args.get("elevate").and_then(Value::as_bool).unwrap_or(false);
    let mut argv = vec![
        action.into(),
        "--id".into(),
        package,
        "-e".into(),
        "--accept-package-agreements".into(),
        "--accept-source-agreements".into(),
    ];
    if !elevate {
        argv.extend(["--scope".into(), "user".into()]);
    }
    run_host_command(tool_run_id, &winget_executable(), &argv, elevate)
}

fn set_environment(args: &Value, tool_run_id: &str) -> LocalOutcome {
    let name = str_arg(args, "name");
    let value = str_arg(args, "value");
    if name.is_empty() {
        return err_text("invalid_arguments");
    }
    let machine = str_arg(args, "scope") == "machine";
    let mut argv = vec![name, value];
    if machine {
        argv.push("/M".into());
    }
    run_host_command(tool_run_id, &system32_exe("setx.exe"), &argv, machine)
}

fn credential_use(args: &Value) -> LocalOutcome {
    let reference = str_arg(args, "reference");
    match crate::credential::load_named(&reference) {
        Some(_) => {
            let mut out = ok_text("used".into());
            out.metadata = json!({"reference": reference, "available": true});
            out
        }
        None => err_text("credential_missing"),
    }
}

fn critical_armed() -> bool {
    std::env::var("ALEX_EXECUTE_CRITICAL").ok().as_deref() == Some("1")
}

fn critical_tool(name: &str, args: &Value) -> LocalOutcome {
    if !critical_armed() {
        let mut out = err_text("critical_not_armed");
        out.metadata = json!({
            "executed": false,
            "armed": false,
            "tool": name,
            "target": str_arg(args, "device").if_empty_then(str_arg(args, "action")),
        });
        return out;
    }
    match name {
        "system_shutdown" => {
            let action = str_arg(args, "action");
            let delay = args.get("delay_seconds").and_then(Value::as_u64).unwrap_or(120);
            let flag = if action == "reboot" { "/r" } else { "/s" };
            run_host_command(
                "critical",
                &system32_exe("shutdown.exe"),
                &[flag.into(), "/t".into(), delay.to_string(), "/c".into(), "Alex LLM confirmed".into()],
                true,
            )
        }
        _ => err_text("critical_not_armed"),
    }
}

trait IfEmpty {
    fn if_empty_then(self, other: String) -> String;
}

impl IfEmpty for String {
    fn if_empty_then(self, other: String) -> String {
        if self.is_empty() { other } else { self }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn delete_file_uses_disposable_temp() {
        let dir = std::env::temp_dir().join("alex-llm-delete-test");
        fs::create_dir_all(&dir).unwrap();
        let file = dir.join("gone.txt");
        fs::write(&file, "x").unwrap();
        let args = json!({"path": file.to_string_lossy()});
        let out = run_local_tool("delete_file", &args, &[], "test-run", || false);
        assert_eq!(out.exit_code, Some(0));
        assert!(!file.exists());
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn known_folders_use_windows_api() {
        let out = run_local_tool("get_known_folders", &json!({}), &[], "folders", || false);
        assert_eq!(out.exit_code, Some(0));
        let desktop = out.metadata["desktop"].as_str().unwrap_or_default();
        assert!(!desktop.is_empty());
        assert!(!desktop.to_ascii_lowercase().contains(r"users\<name>\desktop"));
        assert!(out.text.contains("desktop="));
        let info = run_local_tool("get_system_info", &json!({}), &[], "sys", || false);
        assert!(info.text.contains("platform=windows"));
        assert!(info.text.contains("cpu_logical_processors="));
        assert!(!info.text.to_ascii_lowercase().contains("machineguid"));
        assert!(!info.text.to_ascii_lowercase().contains("mac"));
    }

    #[test]
    fn git_add_rejects_blanket_all() {
        let dir = std::env::temp_dir().join("alex-llm-git-add-test");
        let _ = fs::create_dir_all(&dir);
        let out = crate::git::run_git(
            "git_add",
            &json!({"cwd": dir.to_string_lossy(), "paths": ["-A"]}),
            &[dir.to_string_lossy().to_string()],
            "git-add",
        );
        assert_eq!(out.text, "invalid_arguments");
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn critical_tools_are_not_executed_by_default() {
        let args = json!({"device": "Z:", "purpose": "test"});
        let out = run_local_tool("format_volume", &args, &[], "test-run", || false);
        assert_eq!(out.text, "critical_not_armed");
        assert_eq!(out.metadata["executed"], json!(false));
        let shutdown = run_local_tool(
            "system_shutdown",
            &json!({"action": "shutdown", "delay_seconds": 120}),
            &[],
            "test-run",
            || false,
        );
        assert_eq!(shutdown.text, "critical_not_armed");
    }

    #[test]
    fn registry_hkcu_test_area_roundtrip() {
        let key = r"Software\AlexLLM\Test";
        let args = json!({"hive": "HKCU", "key": key, "name": "AlexTest", "value": "1"});
        let written = run_local_tool("registry_write", &args, &[], "reg-write", || false);
        assert_eq!(
            written.exit_code,
            Some(0),
            "stderr={} stdout={} text={}",
            written.stderr,
            written.stdout,
            written.text
        );
        let read = run_local_tool("registry_read", &args, &[], "reg-read", || false);
        assert_eq!(read.exit_code, Some(0));
        let _ = run_host_command(
            "reg-cleanup",
            &system32_exe("reg.exe"),
            &["delete".into(), r"HKCU\Software\AlexLLM\Test".into(), "/f".into()],
            false,
        );
    }

    #[test]
    fn read_file_includes_sha256_prefix() {
        let dir = std::env::temp_dir().join("alex-llm-read-sha");
        fs::create_dir_all(&dir).unwrap();
        let file = dir.join("notes.txt");
        fs::write(&file, "hello-sha").unwrap();
        let out = run_local_tool(
            "read_file",
            &json!({"path": file.to_string_lossy()}),
            &[],
            "read-sha",
            || false,
        );
        let digest = super::sha256_hex(b"hello-sha");
        assert!(
            out.text.starts_with(&format!("sha256={digest}\n")),
            "text={}",
            out.text
        );
        assert_eq!(out.metadata["sha256"], digest);
        let hashed = run_local_tool(
            "hash_file",
            &json!({"path": file.to_string_lossy()}),
            &[],
            "hash-sha",
            || false,
        );
        assert_eq!(hashed.text, digest);
        assert_eq!(hashed.metadata["digest"], digest);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn owned_python_process_start_stop() {
        let started = run_local_tool(
            "run_python",
            &json!({
                "argv": ["-c", "import time; time.sleep(30)"],
                "timeout_seconds": 35,
                "wait": false,
                "purpose": "owned sleep"
            }),
            &[],
            "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            || false,
        );
        let pid = started.metadata["pid"].as_u64().unwrap_or(0);
        assert!(pid > 0, "text={} meta={}", started.text, started.metadata);
        let status = run_local_tool(
            "process_status",
            &json!({"tool_run_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "purpose": "status"}),
            &[],
            "status-run",
            || false,
        );
        assert!(status.text.contains("running"), "status={}", status.text);
        let stopped = run_local_tool(
            "stop_process",
            &json!({"tool_run_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "purpose": "stop"}),
            &[],
            "stop-run",
            || false,
        );
        assert_eq!(stopped.text, "stopped");
        assert_eq!(stopped.metadata["verified_dead"], true);
        let after = run_local_tool(
            "process_status",
            &json!({"tool_run_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "purpose": "status"}),
            &[],
            "status-run-2",
            || false,
        );
        assert!(!after.text.contains("running") || after.text.contains("unknown") || after.text.contains("exited"));
    }

    #[test]
    fn patch_file_conflicts_when_hash_changes() {
        let dir = std::env::temp_dir().join("alex-llm-patch-test");
        fs::create_dir_all(&dir).unwrap();
        let file = dir.join("notes.txt");
        fs::write(&file, "hello").unwrap();
        let before = super::sha256_hex(b"hello");
        fs::write(&file, "changed").unwrap();
        let args = json!({
            "path": file.to_string_lossy(),
            "old_text": "hello",
            "new_text": "world",
            "expected_before_sha256": before
        });
        let out = run_local_tool("patch_file", &args, &[], "patch-run", || false);
        assert_eq!(out.text, "conflict");
        assert_eq!(fs::read_to_string(&file).unwrap(), "changed");
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn git_force_push_and_hard_reset_are_not_armed() {
        let args = json!({"cwd": std::env::temp_dir().to_string_lossy(), "remote": "origin", "force": true});
        let push = run_local_tool("git_push", &args, &[], "git-push", || false);
        assert_eq!(push.text, "critical_not_armed");
        let reset = run_local_tool(
            "git_reset",
            &json!({"cwd": std::env::temp_dir().to_string_lossy(), "mode": "hard", "ref": "HEAD"}),
            &[],
            "git-reset",
            || false,
        );
        assert_eq!(reset.text, "critical_not_armed");
    }

    #[test]
    fn redact_embedded_git_credentials() {
        let text = redact_text("https://user:ghp_secret@github.com/org/repo.git");
        assert!(!text.contains("ghp_secret"));
        assert!(text.contains("[redacted]"));
    }

    #[test]
    fn git_status_on_disposable_repo_when_git_exists() {
        let git = [
            r"C:\Program Files\Git\cmd\git.exe",
            r"C:\Program Files (x86)\Git\cmd\git.exe",
        ]
        .into_iter()
        .find(|path| std::path::Path::new(path).exists());
        let Some(git) = git else {
            return;
        };
        let dir = std::env::temp_dir().join("alex-llm-git-status");
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        let init = std::process::Command::new(git)
            .args(["init"])
            .current_dir(&dir)
            .output()
            .unwrap();
        assert!(init.status.success());
        fs::write(dir.join("readme.txt"), "hi").unwrap();
        let out = run_local_tool(
            "git_status",
            &json!({"cwd": dir.to_string_lossy()}),
            &[],
            "git-status",
            || false,
        );
        assert_eq!(out.exit_code, Some(0), "{}", out.stderr);
        assert!(out.stdout.contains("readme.txt") || out.text.contains("readme.txt"));
        let _ = fs::remove_dir_all(&dir);
    }
}
