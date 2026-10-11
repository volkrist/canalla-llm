use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use std::fs::{self, File};
use std::io::Write;
use std::path::PathBuf;
use std::sync::{Mutex, OnceLock};
use std::time::Duration;

#[derive(Clone, Serialize, Deserialize)]
pub struct DeviceRecord {
    pub device_id: String,
    pub display_name: String,
    /// Which account and backend this device was paired with. An installation that changes user
    /// or backend must not keep using another identity's device record (A07). Empty on records
    /// written before 1.2.1; they gain the binding on their next successful heartbeat.
    #[serde(default)]
    pub user_id: String,
    #[serde(default)]
    pub backend: String,
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
    let _ = fs::write(
        record_path(),
        serde_json::to_vec(record).unwrap_or_default(),
    );
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
pub async fn pair_device(
    backend_url: String,
    token: String,
    display_name: String,
    user_id: Option<String>,
) -> Result<Value, String> {
    let account = user_id.unwrap_or_default();
    if let (Some(record), Some(_)) = (load_record(), crate::credential::load()) {
        // An explicit change of account or backend must not reuse another identity's device.
        let account_changed =
            !record.user_id.is_empty() && !account.is_empty() && record.user_id != account;
        let backend_changed = !record.backend.is_empty() && record.backend != backend_url;
        if !account_changed && !backend_changed {
            let client = local_client();
            let heartbeat = client
                .post(format!("{backend_url}/tools/devices/heartbeat"))
                .headers(device_headers(&token)?)
                .send()
                .await;
            if matches!(&heartbeat, Ok(response) if response.status() == reqwest::StatusCode::UNAUTHORIZED)
            {
                // The stored credential belongs to a different account: forget it and pair fresh.
                let _ = crate::credential::delete();
                let _ = fs::remove_file(record_path());
            } else {
                // `online` is the verified heartbeat answer, never the bare presence of a credential.
                let online = matches!(&heartbeat, Ok(response) if response.status().is_success());
                if online && (record.user_id.is_empty() || record.backend.is_empty()) {
                    save_record(&DeviceRecord {
                        user_id: if record.user_id.is_empty() {
                            account.clone()
                        } else {
                            record.user_id.clone()
                        },
                        backend: if record.backend.is_empty() {
                            backend_url.clone()
                        } else {
                            record.backend.clone()
                        },
                        ..record.clone()
                    });
                }
                return Ok(json!({
                    "device_id": record.device_id,
                    "display_name": record.display_name,
                    "online": online,
                    "storage": crate::credential::storage_kind(),
                }));
            }
        } else {
            let _ = crate::credential::delete();
            let _ = fs::remove_file(record_path());
        }
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
    let credential = body
        .get("credential")
        .and_then(Value::as_str)
        .ok_or("pair_failed")?;
    crate::credential::store(credential)?;
    let record = DeviceRecord {
        device_id,
        display_name: body
            .get("display_name")
            .and_then(Value::as_str)
            .unwrap_or("Windows device")
            .to_string(),
        user_id: account,
        backend: backend_url.clone(),
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
            .post(format!(
                "{backend_url}/tools/devices/{}/forget",
                record.device_id
            ))
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
        .post(format!(
            "{backend_url}/tools/devices/{}/rotate",
            record.device_id
        ))
        .bearer_auth(&token)
        .send()
        .await
        .map_err(|e| e.to_string())?;
    let body: Value = response.json().await.map_err(|e| e.to_string())?;
    let credential = body
        .get("credential")
        .and_then(Value::as_str)
        .ok_or("rotate_failed")?;
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

/// The native journal: what this device has already started or finished.
///
/// It is the reason a lost result POST (or a crash of this process) can never make a command
/// run twice. `started` means the side effect may already have happened; `done` holds the
/// result that must be re-delivered; `unknown` means the outcome could not be decided and
/// must never be replayed automatically.
#[derive(Clone, Serialize, Deserialize)]
struct JournalEntry {
    claim_id: String,
    digest: String,
    phase: String,
    #[serde(default)]
    result: Option<Value>,
}

fn journal_path() -> PathBuf {
    data_dir().join("host-journal.json")
}

/// Serializes journal read-modify-write cycles. The file is the source of truth, so a fresh
/// process (or a test with its own `ALEX_DEVICE_DIR`) sees exactly what the last write left.
fn journal_lock() -> &'static Mutex<()> {
    static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
    LOCK.get_or_init(|| Mutex::new(()))
}

fn journal_load() -> HashMap<String, JournalEntry> {
    fs::read_to_string(journal_path())
        .ok()
        .and_then(|text| serde_json::from_str::<HashMap<String, JournalEntry>>(&text).ok())
        .unwrap_or_default()
}

fn persist_journal(map: &HashMap<String, JournalEntry>) {
    let tmp = journal_path().with_extension("json.tmp");
    if let Ok(mut file) = File::create(&tmp) {
        if file
            .write_all(&serde_json::to_vec(map).unwrap_or_default())
            .is_ok()
        {
            let _ = file.sync_all();
            let _ = fs::rename(&tmp, journal_path());
        }
    }
}

fn journal_read(run_id: &str) -> Option<JournalEntry> {
    let _guard = journal_lock().lock().ok()?;
    journal_load().remove(run_id)
}

fn journal_write(run_id: &str, entry: JournalEntry) {
    if let Ok(_guard) = journal_lock().lock() {
        let mut map = journal_load();
        map.insert(run_id.to_string(), entry);
        persist_journal(&map);
    }
}

fn journal_remove(run_id: &str) {
    if let Ok(_guard) = journal_lock().lock() {
        let mut map = journal_load();
        if map.remove(run_id).is_some() {
            persist_journal(&map);
        }
    }
}

/// Test seam: forget every local journal entry and delete the file.
pub fn reset_journal() {
    let _guard = journal_lock().lock();
    let _ = fs::remove_file(journal_path());
}

/// A POST with the device headers; `Err(())` means it never reached the backend.
async fn backend_post(
    client: &reqwest::Client,
    url: &str,
    token: &str,
    body: &Value,
) -> Result<reqwest::StatusCode, ()> {
    let Ok(headers) = device_headers(token) else {
        return Err(());
    };
    match client.post(url).headers(headers).json(body).send().await {
        Ok(response) => Ok(response.status()),
        Err(_) => Err(()),
    }
}

/// A POST that refreshes the access token once if the current one is rejected. The refresh
/// lives in the native layer, so the host keeps working after the JWT rolls over without the
/// app being restarted.
async fn authed_post(
    client: &reqwest::Client,
    base: &str,
    token: &mut String,
    url: &str,
    body: &Value,
) -> Option<reqwest::StatusCode> {
    match backend_post(client, url, token, body).await {
        Ok(status) if status != reqwest::StatusCode::UNAUTHORIZED => return Some(status),
        _ => {}
    }
    let fresh = refresh_token(base).await?;
    *token = fresh;
    backend_post(client, url, token, body).await.ok()
}

/// Rotate the persistent session into a fresh access token. Single-flight lives inside
/// ``session`` so a host refresh and a webview restore cannot replay a consumed refresh secret
/// against each other.
async fn refresh_token(backend_url: &str) -> Option<String> {
    crate::session::refresh_access_token(backend_url).await
}

/// Fetch this device's jobs, refreshing the token once if it has expired.
async fn fetch_jobs(
    client: &reqwest::Client,
    base: &str,
    token: &str,
) -> Result<(Vec<Value>, String), String> {
    let url = format!("{base}/tools/devices/jobs");
    let headers = device_headers(token)?;
    match client.get(&url).headers(headers).send().await {
        Ok(response) if response.status().is_success() => {
            let jobs = response.json().await.map_err(|error| error.to_string())?;
            Ok((jobs, token.to_string()))
        }
        Ok(response) if response.status() == reqwest::StatusCode::UNAUTHORIZED => {
            let fresh = refresh_token(base).await.ok_or("unauthorized")?;
            let headers = device_headers(&fresh)?;
            let retry = client
                .get(&url)
                .headers(headers)
                .send()
                .await
                .map_err(|error| error.to_string())?;
            if !retry.status().is_success() {
                return Err("unauthorized".into());
            }
            let jobs = retry.json().await.map_err(|error| error.to_string())?;
            Ok((jobs, fresh))
        }
        Ok(response) => Err(format!("jobs_status_{}", response.status().as_u16())),
        Err(error) => Err(error.to_string()),
    }
}

/// Deliver a stored result and acknowledge it. Returns true only once the backend has it.
async fn deliver_and_ack(
    client: &reqwest::Client,
    base: &str,
    token: &mut String,
    run_id: &str,
    body: &Value,
) -> bool {
    let result_url = format!("{base}/tools/runs/{run_id}/host-result");
    match authed_post(client, base, token, &result_url, body).await {
        Some(status) if status.is_success() => {}
        _ => return false,
    }
    let claim_id = body.get("claim_id").cloned().unwrap_or_else(|| json!(""));
    let ack_url = format!("{base}/tools/runs/{run_id}/host-ack");
    matches!(
        authed_post(client, base, token, &ack_url, &json!({"claim_id": claim_id})).await,
        Some(status) if status.is_success()
    )
}

/// Decline a job whose effect may already have run. Never a success, never a replay.
async fn report_unknown(
    client: &reqwest::Client,
    base: &str,
    token: &str,
    run_id: &str,
    claim_id: &str,
    digest: &str,
) {
    let url = format!("{base}/tools/runs/{run_id}/host-result");
    let body = json!({
        "digest": digest,
        "claim_id": claim_id,
        "status": "unknown",
        "exit_code": Value::Null,
        "stdout": "",
        "stderr": "",
        "text": "",
        "metadata": {},
    });
    let _ = backend_post(client, &url, token, &body).await;
}

/// One job, from claim to acknowledged result. The journal decides whether the command may run
/// at all: a stored result is re-delivered, a started-but-unfinished entry is declined.
async fn handle_job(
    client: reqwest::Client,
    backend: String,
    mut token: String,
    run_id: String,
    claim_id: String,
    digest: String,
    name: String,
    args: Value,
    roots: Vec<String>,
) {
    if let Some(entry) = journal_read(&run_id) {
        let phase = entry.phase.clone();
        match phase.as_str() {
            "done" => {
                if let Some(result) = entry.result.clone() {
                    if deliver_and_ack(&client, &backend, &mut token, &run_id, &result).await {
                        journal_remove(&run_id);
                    }
                }
                return;
            }
            "unknown" => return,
            "started" => {
                report_unknown(
                    &client,
                    &backend,
                    &token,
                    &run_id,
                    &entry.claim_id,
                    &entry.digest,
                )
                .await;
                journal_write(
                    &run_id,
                    JournalEntry {
                        phase: "unknown".into(),
                        ..entry
                    },
                );
                return;
            }
            _ => {}
        }
    }
    let started_url = format!("{backend}/tools/runs/{run_id}/host-started");
    let _ = backend_post(
        &client,
        &started_url,
        &token,
        &json!({"claim_id": claim_id}),
    )
    .await;
    journal_write(
        &run_id,
        JournalEntry {
            claim_id: claim_id.clone(),
            digest: digest.clone(),
            phase: "started".into(),
            result: None,
        },
    );
    let run_id_for_tool = run_id.clone();
    let stop_token = token.clone();
    let backend_stop = backend.clone();
    let outcome = tokio::task::spawn_blocking(move || {
        let stop_id = run_id_for_tool.clone();
        run_local_tool(&name, &args, &roots, &run_id_for_tool, move || {
            poll_stopped(&backend_stop, &stop_token, &stop_id)
        })
    })
    .await;
    let Ok(outcome) = outcome else {
        return;
    };
    let result = json!({
        "digest": digest,
        "claim_id": claim_id,
        "status": "completed",
        "exit_code": outcome.exit_code,
        "stdout": outcome.stdout,
        "stderr": outcome.stderr,
        "text": outcome.text,
        "metadata": outcome.metadata,
    });
    journal_write(
        &run_id,
        JournalEntry {
            claim_id: claim_id.clone(),
            digest: digest.clone(),
            phase: "done".into(),
            result: Some(result.clone()),
        },
    );
    if deliver_and_ack(&client, &backend, &mut token, &run_id, &result).await {
        journal_remove(&run_id);
    }
}

#[tauri::command]
pub async fn execute_host_jobs(
    backend_url: String,
    token: String,
    roots: Vec<String>,
) -> Result<Value, String> {
    let client = local_client();
    let (jobs, token) = fetch_jobs(&client, &backend_url, &token).await?;
    let mut started = 0;
    for job in jobs {
        let id = job
            .get("id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
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
        let digest = job
            .get("input_digest")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let name = job
            .get("tool_name")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let args = job
            .get("host_args")
            .cloned()
            .unwrap_or(Value::Object(Default::default()));
        let claim_id = job
            .get("claim_id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let backend = backend_url.clone();
        let token_job = token.clone();
        let run_id = id.clone();
        let roots_for_job = roots.clone();
        let client_job = client.clone();
        tokio::spawn(async move {
            handle_job(
                client_job,
                backend,
                token_job,
                run_id,
                claim_id,
                digest,
                name,
                args,
                roots_for_job,
            )
            .await;
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
    let client = match reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(2))
        .build()
    {
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
    // The vocabulary is the same on every platform - the model is told about the same tool names -
    // but a tool that only exists on Windows says so here instead of trying to run a Windows binary.
    #[cfg(not(windows))]
    if WINDOWS_ONLY_TOOLS.contains(&name) {
        return windows_only_tool();
    }
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
        "run_process" | "run_powershell" | "run_python" => {
            run_exec(name, args, roots, tool_run_id, should_stop)
        }
        "stop_process" => {
            let pid = crate::process::stop_job(&str_arg(args, "tool_run_id"));
            let mut out = ok_text("stopped".into());
            out.metadata = json!({"pid": pid, "verified_dead": true});
            out
        }
        "process_status" => {
            ok_text(crate::process::job_status(&str_arg(args, "tool_run_id")).into())
        }
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
        "format_volume" | "manage_partition" | "boot_config" | "bitlocker_change"
        | "system_shutdown" => critical_tool(name, args),
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

fn known_folders() -> LocalOutcome {
    let (desktop, documents, downloads) = crate::platform::known_folders();
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

fn system_info() -> LocalOutcome {
    let facts = crate::platform::system_info();
    let text = format!(
        "platform={}\nos_version={}\ncpu_logical_processors={}\nram_total_mb={}\nram_avail_mb={}\nsystem_disk_free_gb={}",
        facts.platform,
        facts.os_version,
        facts.cpu_logical_processors,
        facts.ram_total_mb,
        facts.ram_avail_mb,
        facts.disk_free_gb
    );
    let mut out = ok_text(text);
    out.metadata = json!({
        "platform": facts.platform,
        "os_version": facts.os_version,
        "cpu_logical_processors": facts.cpu_logical_processors,
        "ram_total_mb": facts.ram_total_mb,
        "ram_avail_mb": facts.ram_avail_mb,
        "system_disk_free_gb": facts.disk_free_gb,
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
            let body: String = String::from_utf8_lossy(&bytes)
                .chars()
                .take(20000)
                .collect();
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
    crate::platform::replace_file(tmp, dest)
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
    let (Ok(from), Ok(to)) = (
        crate::fs_guard::resolve(&src, roots),
        crate::fs_guard::resolve(&dst, roots),
    ) else {
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
    let (Ok(from), Ok(to)) = (
        crate::fs_guard::resolve(&src, roots),
        crate::fs_guard::resolve(&dst, roots),
    ) else {
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
    fn walk(
        dir: &std::path::Path,
        query: &str,
        hits: &mut Vec<String>,
        files: &mut u32,
        depth: u32,
    ) {
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
                    hits.push(format!(
                        "{}:{}:{}",
                        path.to_string_lossy(),
                        index + 1,
                        line.trim()
                    ));
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
    let old_text = args
        .get("old_text")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let new_text = args
        .get("new_text")
        .and_then(Value::as_str)
        .unwrap_or_default();
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
    // PowerShell is a Windows tool: on a platform that has no such thing the honest answer is that
    // it does not exist here, rather than an attempt to run a Windows binary.
    #[cfg(not(windows))]
    if name == "run_powershell" {
        return windows_only_tool();
    }
    argv.extend(extra);
    let elevate = args
        .get("elevate")
        .and_then(Value::as_bool)
        .unwrap_or(false);
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
    #[cfg(windows)]
    {
        if let Ok(local) = std::env::var("LOCALAPPDATA") {
            let root = std::path::PathBuf::from(local)
                .join("Programs")
                .join("Python");
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
            if std::path::Path::new(&candidate).is_file() {
                return candidate;
            }
        }
        return "python.exe".to_string();
    }
    // POSIX has no fixed install location: the interpreter is on `PATH`, and a distribution that
    // ships one without `python3` is not a desktop anyone is using.
    #[cfg(not(windows))]
    {
        which_on_path("python3").unwrap_or_else(|| "python3".to_string())
    }
}

/// The first `name` on `PATH` that is a file, or `None`.
#[cfg(not(windows))]
fn which_on_path(name: &str) -> Option<String> {
    let path = std::env::var_os("PATH")?;
    std::env::split_paths(&path)
        .map(|dir| dir.join(name))
        .find(|candidate| candidate.is_file())
        .map(|candidate| candidate.to_string_lossy().into_owned())
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

/// A Windows-only tool asked for on a platform that has no such thing: the honest answer is that
/// the tool does not exist here, not a failed attempt to run a Windows binary.
#[cfg_attr(windows, allow(dead_code))]
fn windows_only_tool() -> LocalOutcome {
    err_text("unsupported_platform")
}

/// The tools that describe a Windows machine: its registry, its services, `tasklist`/`wmic`,
/// `schtasks`, `netsh`, `setx` and `winget`. On another platform they answer
/// `unsupported_platform` rather than pretending.
#[allow(dead_code)]
const WINDOWS_ONLY_TOOLS: &[&str] = &[
    "list_processes",
    "inspect_process",
    "list_volumes",
    "list_installed_software",
    "registry_read",
    "read_registry",
    "registry_write",
    "write_registry",
    "delete_registry_value",
    "delete_registry_key",
    "windows_service_status",
    "query_service",
    "windows_service_control",
    "start_service",
    "stop_service",
    "restart_service",
    "change_service_settings",
    "scheduled_task",
    "firewall_rule",
    "install_software",
    "uninstall_software",
    "set_environment",
    "system_shutdown",
];

/// The path of a Windows system binary. The *tools* that use it are Windows-only and are gated in
/// [`run_local_tool`]; this is only the path builder, so it stays available on every platform.
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
    #[cfg(not(windows))]
    {
        // There is no registry on this platform. The tool answers honestly instead of trying to run
        // a Windows binary that does not exist here.
        let _ = (action, args, tool_run_id);
        return err_text("unsupported_platform");
    }
    #[cfg(windows)]
    {
        registry_op_windows(action, args, tool_run_id)
    }
}

#[cfg(windows)]
fn registry_op_windows(action: &str, args: &Value, tool_run_id: &str) -> LocalOutcome {
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
    let elevate = hive == "HKLM"
        || args
            .get("elevate")
            .and_then(Value::as_bool)
            .unwrap_or(false);
    match action {
        "add" => {
            let value = str_arg(args, "value");
            let mut argv = vec!["add".into(), path, "/f".into()];
            if !name.is_empty() {
                argv.extend([
                    "/v".into(),
                    name,
                    "/d".into(),
                    value,
                    "/t".into(),
                    "REG_SZ".into(),
                ]);
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
    #[cfg(not(windows))]
    {
        let _ = (args, tool_run_id, control);
        return windows_only_tool();
    }
    #[cfg(windows)]
    {
        service_op_windows(args, tool_run_id, control)
    }
}

#[cfg(windows)]
fn service_op_windows(args: &Value, tool_run_id: &str, control: bool) -> LocalOutcome {
    let name = str_arg(args, "name");
    if name.is_empty() {
        return err_text("invalid_arguments");
    }
    let action = str_arg(args, "action");
    if !control {
        return run_host_command(
            tool_run_id,
            &system32_exe("sc.exe"),
            &["query".into(), name],
            false,
        );
    }
    let verb = if action == "stop" { "stop" } else { "start" };
    run_host_command(
        tool_run_id,
        &system32_exe("sc.exe"),
        &[verb.into(), name],
        true,
    )
}

fn service_named(tool: &str, args: &Value, tool_run_id: &str) -> LocalOutcome {
    #[cfg(not(windows))]
    {
        let _ = (tool, args, tool_run_id);
        return windows_only_tool();
    }
    #[cfg(windows)]
    {
        service_named_windows(tool, args, tool_run_id)
    }
}

#[cfg(windows)]
fn service_named_windows(tool: &str, args: &Value, tool_run_id: &str) -> LocalOutcome {
    let name = str_arg(args, "name");
    if name.is_empty() {
        return err_text("invalid_arguments");
    }
    match tool {
        "start_service" => run_host_command(
            tool_run_id,
            &system32_exe("sc.exe"),
            &["start".into(), name],
            true,
        ),
        "stop_service" => run_host_command(
            tool_run_id,
            &system32_exe("sc.exe"),
            &["stop".into(), name],
            true,
        ),
        "restart_service" => {
            let _ = run_host_command(
                tool_run_id,
                &system32_exe("sc.exe"),
                &["stop".into(), name.clone()],
                true,
            );
            run_host_command(
                tool_run_id,
                &system32_exe("sc.exe"),
                &["start".into(), name],
                true,
            )
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
        &[
            "/FI".into(),
            format!("PID eq {pid}"),
            "/FO".into(),
            "LIST".into(),
        ],
        false,
    );
    let kept: Vec<String> = out
        .stdout
        .lines()
        .filter(|line| {
            let lower = line.to_ascii_lowercase();
            lower.starts_with("image name:")
                || lower.starts_with("pid:")
                || lower.starts_with("session")
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
        _ => vec![
            "/Query".into(),
            "/TN".into(),
            name,
            "/FO".into(),
            "LIST".into(),
        ],
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
        _ => vec![
            "advfirewall".into(),
            "firewall".into(),
            "show".into(),
            "rule".into(),
            format!("name={name}"),
        ],
    };
    run_host_command(
        tool_run_id,
        &system32_exe("netsh.exe"),
        &argv,
        action != "list",
    )
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
    let elevate = args
        .get("elevate")
        .and_then(Value::as_bool)
        .unwrap_or(false);
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
            let delay = args
                .get("delay_seconds")
                .and_then(Value::as_u64)
                .unwrap_or(120);
            let flag = if action == "reboot" { "/r" } else { "/s" };
            run_host_command(
                "critical",
                &system32_exe("shutdown.exe"),
                &[
                    flag.into(),
                    "/t".into(),
                    delay.to_string(),
                    "/c".into(),
                    "Alex LLM confirmed".into(),
                ],
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
        if self.is_empty() {
            other
        } else {
            self
        }
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

    #[cfg(windows)]
    #[test]
    fn known_folders_use_windows_api() {
        let out = run_local_tool("get_known_folders", &json!({}), &[], "folders", || false);
        assert_eq!(out.exit_code, Some(0));
        let desktop = out.metadata["desktop"].as_str().unwrap_or_default();
        assert!(!desktop.is_empty());
        assert!(!desktop
            .to_ascii_lowercase()
            .contains(r"users\<name>\desktop"));
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
        // The property is the same on every platform: nothing critical runs without an explicit
        // arm. Windows refuses with `critical_not_armed`; a platform that has no such tool at all
        // says so instead - never a success either way.
        assert!(
            matches!(
                shutdown.text.as_str(),
                "critical_not_armed" | "unsupported_platform"
            ),
            "{}",
            shutdown.text
        );
        assert_ne!(shutdown.exit_code, Some(0));
    }

    #[cfg(windows)]
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
            &[
                "delete".into(),
                r"HKCU\Software\AlexLLM\Test".into(),
                "/f".into(),
            ],
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
        assert!(
            !after.text.contains("running")
                || after.text.contains("unknown")
                || after.text.contains("exited")
        );
    }

    /// A15: a child that writes far more than the OS pipe buffer must not deadlock the runner.
    /// The old runner read stdout/stderr only after the child exited.
    #[test]
    fn run_python_large_output_does_not_deadlock() {
        let script = "import sys\nfor _ in range(3000):\n    sys.stdout.write('x'*1000)\n";
        let out = run_local_tool(
            "run_python",
            &json!({"argv": ["-c", script], "timeout_seconds": 60, "purpose": "large output"}),
            &[],
            "large-output-run",
            || false,
        );
        assert_eq!(
            out.exit_code,
            Some(0),
            "stderr={} text={}",
            out.stderr,
            out.text
        );
        assert!(
            out.stdout.len() <= 20000,
            "stdout was not bounded: {}",
            out.stdout.len()
        );
        assert!(out.stdout.starts_with("xxx"));
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

    fn journal_dir(tag: &str) -> (PathBuf, Option<String>) {
        let previous = std::env::var("ALEX_DEVICE_DIR").ok();
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|duration| duration.as_nanos())
            .unwrap_or(0);
        let dir = std::env::temp_dir().join(format!("alex-host-journal-{tag}-{nanos}"));
        let _ = fs::create_dir_all(&dir);
        std::env::set_var("ALEX_DEVICE_DIR", &dir);
        reset_journal();
        (dir, previous)
    }

    fn restore_journal_dir(previous: Option<String>) {
        match previous {
            Some(value) => std::env::set_var("ALEX_DEVICE_DIR", value),
            None => std::env::remove_var("ALEX_DEVICE_DIR"),
        }
    }

    /// A crash after the effect is a `started` marker on disk: the next process must see it and
    /// decline, not run the command a second time.
    #[test]
    fn journal_survives_a_process_restart() {
        let _guard = crate::credential::TEST_ENV_LOCK.lock().unwrap();
        let (dir, previous) = journal_dir("restart");
        journal_write(
            "run-1",
            JournalEntry {
                claim_id: "c1".into(),
                digest: "d1".into(),
                phase: "started".into(),
                result: None,
            },
        );
        let loaded: HashMap<String, JournalEntry> =
            serde_json::from_str(&fs::read_to_string(journal_path()).unwrap()).unwrap();
        let entry = loaded.get("run-1").unwrap();
        assert_eq!(entry.claim_id, "c1");
        assert_eq!(entry.phase, "started");
        assert!(entry.result.is_none());
        reset_journal();
        assert!(!journal_path().exists());
        restore_journal_dir(previous);
        let _ = fs::remove_dir_all(&dir);
    }

    /// Once the result exists it is held on disk, so a lost POST is a re-read, never a re-run.
    #[test]
    fn journal_keeps_the_result_for_redelivery() {
        let _guard = crate::credential::TEST_ENV_LOCK.lock().unwrap();
        let (dir, previous) = journal_dir("done");
        let result = json!({
            "digest": "d1",
            "claim_id": "c1",
            "status": "completed",
            "exit_code": 0,
            "text": "x",
        });
        journal_write(
            "run-2",
            JournalEntry {
                claim_id: "c1".into(),
                digest: "d1".into(),
                phase: "done".into(),
                result: Some(result),
            },
        );
        let loaded: HashMap<String, JournalEntry> =
            serde_json::from_str(&fs::read_to_string(journal_path()).unwrap()).unwrap();
        let entry = loaded.get("run-2").unwrap();
        assert_eq!(entry.phase, "done");
        assert_eq!(entry.result.as_ref().unwrap()["text"], "x");
        assert_eq!(journal_read("run-2").unwrap().phase, "done");
        journal_remove("run-2");
        assert!(journal_read("run-2").is_none());
        restore_journal_dir(previous);
        let _ = fs::remove_dir_all(&dir);
    }
}
