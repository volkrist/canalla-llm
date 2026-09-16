use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::fs::{self, File};
use std::io::Write;
use std::os::windows::ffi::OsStrExt;
use std::path::PathBuf;
use std::time::Duration;

#[derive(Clone, Serialize, Deserialize)]
pub struct DeviceRecord {
    pub device_id: String,
    pub display_name: String,
}

fn data_dir() -> PathBuf {
    let root = std::env::var("LOCALAPPDATA").unwrap_or_else(|_| ".".into());
    let dir = PathBuf::from(root).join("Alex LLM");
    let _ = fs::create_dir_all(&dir);
    dir
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

fn device_headers(token: &str) -> Result<reqwest::header::HeaderMap, String> {
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

#[tauri::command]
pub async fn pair_device(backend_url: String, token: String, display_name: String) -> Result<Value, String> {
    if let (Some(record), Some(_)) = (load_record(), crate::credential::load()) {
        let client = reqwest::Client::new();
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
    let client = reqwest::Client::new();
    let response = client
        .post(format!("{backend_url}/tools/devices/pair"))
        .bearer_auth(&token)
        .json(&json!({
            "display_name": display_name,
            "platform": "windows",
            "capabilities": ["fs", "process"]
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
            .unwrap_or("Alex-PC")
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
pub async fn execute_host_jobs(backend_url: String, token: String, roots: Vec<String>) -> Result<Value, String> {
    let client = reqwest::Client::new();
    let headers = device_headers(&token)?;
    let response = client
        .get(format!("{backend_url}/tools/devices/jobs"))
        .headers(headers.clone())
        .send()
        .await
        .map_err(|e| e.to_string())?;
    let jobs: Vec<Value> = response.json().await.map_err(|e| e.to_string())?;
    let mut done = 0;
    for job in jobs {
        let id = job.get("id").and_then(Value::as_str).unwrap_or_default().to_string();
        let digest = job.get("input_digest").and_then(Value::as_str).unwrap_or_default().to_string();
        let name = job.get("tool_name").and_then(Value::as_str).unwrap_or_default().to_string();
        let args = job.get("host_args").cloned().unwrap_or(Value::Object(Default::default()));
        let backend = backend_url.clone();
        let token_copy = token.clone();
        let run_id = id.clone();
        let roots_for_job = roots.clone();
        let result = tokio::task::spawn_blocking(move || {
            let stop_id = run_id.clone();
            run_local_tool(&name, &args, &roots_for_job, &run_id, move || {
                poll_stopped(&backend, &token_copy, &stop_id)
            })
        })
        .await
        .map_err(|e| e.to_string())?;
        let _ = client
            .post(format!("{backend_url}/tools/runs/{id}/host-result"))
            .headers(device_headers(&token)?)
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
        done += 1;
    }
    let _ = client
        .post(format!("{backend_url}/tools/devices/heartbeat"))
        .headers(device_headers(&token)?)
        .send()
        .await;
    Ok(json!({"completed": done}))
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
        "get_system_info" => ok_text("platform=windows".into()),
        "list_directory" => fs_list(str_arg(args, "path"), roots),
        "read_file" => fs_read(str_arg(args, "path"), roots),
        "write_file" => fs_write(args, roots),
        "create_directory" => fs_create_dir(str_arg(args, "path"), roots),
        "copy_file" => fs_copy(args, roots),
        "move_file" => fs_move(args, roots),
        "search_files" => fs_search(args, roots),
        "run_process" | "run_powershell" | "run_python" => run_exec(name, args, roots, tool_run_id, should_stop),
        "stop_process" => {
            crate::process::stop_job(&str_arg(args, "tool_run_id"));
            ok_text("stopped".into())
        }
        "process_status" => ok_text("unknown".into()),
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
    match fs::read_to_string(&file) {
        Ok(text) => ok_text(text.chars().take(20000).collect()),
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
            _ => return err_text("hash_mismatch"),
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
        "files_changed": 1
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
    if let Ok(entries) = fs::read_dir(dir) {
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().to_string();
            if name.to_lowercase().contains(&query) {
                hits.push(name);
            }
        }
    }
    ok_text(hits.join("\n"))
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
        .or_else(|| roots.first().cloned());
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
        "run_python" => (python_executable(), vec!["-3".into()]),
        _ => (str_arg(args, "executable"), vec![]),
    };
    if exe.is_empty() {
        return err_text("invalid_arguments");
    }
    argv.extend(extra);
    crate::process::run_job(&tool_run_id, &exe, &argv, cwd.as_deref(), Duration::from_secs(timeout), should_stop)
}

fn python_executable() -> String {
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
