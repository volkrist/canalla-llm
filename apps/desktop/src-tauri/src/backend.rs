//! Desktop-owned local backend supervisor.
//!
//! Owns at most one Alex backend process via a Job Object. Never kills by
//! image name. An already-healthy Alex API is reused; an unrelated occupant of
//! the preferred port is skipped instead of being terminated.

use serde::Serialize;
use serde_json::{json, Value};
use std::fs;
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};
use uuid_lite::UuidLite;

const PREFERRED_PORT: u16 = 8000;
const PORT_SPAN: u16 = 20;
const STARTUP_TIMEOUT: Duration = Duration::from_secs(45);
const HEALTH_TIMEOUT: Duration = Duration::from_millis(400);

#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum BackendState {
    Starting,
    Ready,
    Error,
}

#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Ownership {
    None,
    Owned,
    External,
}

#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum RuntimeMode {
    None,
    Packaged,
    DevOwned,
    DevExternal,
}

const EXPECTED_PROTOCOL: u64 = 1;

#[derive(Clone, Debug, Serialize)]
pub struct BackendStatus {
    pub state: BackendState,
    pub ownership: Ownership,
    pub url: Option<String>,
    pub port: Option<u16>,
    pub pid: Option<u32>,
    pub error: Option<String>,
    pub data_dir: String,
    pub runtime_mode: RuntimeMode,
    pub diagnostic: Option<String>,
}

impl BackendStatus {
    fn error(code: &str, data_dir: PathBuf) -> Self {
        Self {
            state: BackendState::Error,
            ownership: Ownership::None,
            url: None,
            port: None,
            pid: None,
            error: Some(code.into()),
            data_dir: data_dir.to_string_lossy().into_owned(),
            runtime_mode: RuntimeMode::None,
            diagnostic: None,
        }
    }
}

struct Live {
    child: Option<Child>,
    #[allow(dead_code)]
    job: Option<OwnedJob>,
    pid: u32,
    port: u16,
    #[allow(dead_code)]
    instance: String,
    ownership: Ownership,
    mode: RuntimeMode,
    restarts: u8,
}

struct Supervisor {
    live: Option<Live>,
    last: BackendStatus,
}

struct OwnedJob(std::os::windows::io::OwnedHandle);

fn supervisor() -> &'static Mutex<Supervisor> {
    static CELL: std::sync::OnceLock<Mutex<Supervisor>> = std::sync::OnceLock::new();
    CELL.get_or_init(|| {
        Mutex::new(Supervisor {
            live: None,
            last: BackendStatus {
                state: BackendState::Starting,
                ownership: Ownership::None,
                url: None,
                port: None,
                pid: None,
                error: None,
                data_dir: data_root().to_string_lossy().into_owned(),
                runtime_mode: RuntimeMode::None,
                diagnostic: None,
            },
        })
    })
}

pub fn data_root() -> PathBuf {
    if let Ok(dir) = std::env::var("ALEX_LLM_DATA_DIR") {
        let path = PathBuf::from(dir);
        if !path.as_os_str().is_empty() {
            return path;
        }
    }
    crate::credential::data_dir()
}

#[allow(dead_code)]
pub fn redact_secret(text: &str, secret: &str) -> String {
    if secret.is_empty() {
        return text.to_string();
    }
    text.replace(secret, "[redacted]")
}

fn url_for(port: u16) -> String {
    format!("http://127.0.0.1:{port}")
}

fn port_connectable(port: u16) -> bool {
    TcpStream::connect_timeout(
        &format!("127.0.0.1:{port}").parse().unwrap(),
        Duration::from_millis(150),
    )
    .is_ok()
}

pub fn alex_health(url: &str) -> Option<Value> {
    let client = reqwest::blocking::Client::builder()
        .timeout(HEALTH_TIMEOUT)
        .build()
        .ok()?;
    let response = client.get(format!("{url}/health")).send().ok()?;
    if !response.status().is_success() {
        return None;
    }
    let body: Value = response.json().ok()?;
    if body.get("status")?.as_str()? != "ok" {
        return None;
    }
    let product = body.get("product").and_then(Value::as_str);
    if product == Some("alex-llm") || body.get("provider").is_some() {
        Some(body)
    } else {
        None
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum PortKind {
    Free,
    Alex,
    Unrelated,
}

pub fn classify_port(port: u16) -> PortKind {
    if let Some(_) = alex_health(&url_for(port)) {
        return PortKind::Alex;
    }
    if port_connectable(port) {
        PortKind::Unrelated
    } else {
        PortKind::Free
    }
}

pub fn select_listen_port(preferred: u16) -> Result<u16, String> {
    for port in preferred..preferred.saturating_add(PORT_SPAN) {
        match classify_port(port) {
            PortKind::Free => return Ok(port),
            PortKind::Alex => return Ok(port),
            PortKind::Unrelated => continue,
        }
    }
    Err("NO_SAFE_BACKEND_PORT".into())
}

fn layout(root: &Path) -> Result<(PathBuf, PathBuf, PathBuf, PathBuf, PathBuf), String> {
    let data = root.join("data");
    let documents = root.join("documents");
    let logs = root.join("logs");
    let runtime = root.join("runtime");
    let models = root.join("models").join("embeddings");
    for dir in [&data, &documents, &logs, &runtime, &models] {
        fs::create_dir_all(dir).map_err(|_| "DATA_ROOT_UNAVAILABLE".to_string())?;
    }
    Ok((data, documents, logs, runtime, models))
}

pub fn load_or_create_jwt(root: &Path) -> Result<String, String> {
    let path = root.join("runtime").join("jwt.secret");
    if let Ok(existing) = fs::read_to_string(&path) {
        let trimmed = existing.trim();
        if trimmed.len() >= 48 {
            return Ok(trimmed.to_string());
        }
    }
    let secret = UuidLite::secret();
    fs::create_dir_all(path.parent().unwrap()).map_err(|_| "JWT_SECRET_FAILED".to_string())?;
    fs::write(&path, &secret).map_err(|_| "JWT_SECRET_FAILED".to_string())?;
    Ok(secret)
}

pub fn load_or_create_runtime_token(root: &Path) -> Result<String, String> {
    let path = root.join("runtime").join("shutdown.token");
    if let Ok(existing) = fs::read_to_string(&path) {
        let trimmed = existing.trim();
        if trimmed.len() >= 32 {
            return Ok(trimmed.to_string());
        }
    }
    let token = UuidLite::secret();
    fs::create_dir_all(path.parent().unwrap()).map_err(|e| e.to_string())?;
    fs::write(&path, &token).map_err(|e| e.to_string())?;
    Ok(token)
}

fn request_managed_shutdown(port: u16, token: &str) {
    if port == 0 || token.is_empty() {
        return;
    }
    let Ok(client) = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(12))
        .build()
    else {
        return;
    };
    let _ = client
        .post(format!("http://127.0.0.1:{port}/runtime/shutdown"))
        .header("X-Alex-Runtime-Token", token)
        .send();
}

fn sqlite_url(db: &Path) -> String {
    format!("sqlite:///{}", db.to_string_lossy().replace('\\', "/"))
}

fn packaged_required() -> bool {
    match std::env::var("ALEX_RUNTIME_MODE") {
        Ok(value) if value.eq_ignore_ascii_case("packaged") => true,
        Ok(value) if value.eq_ignore_ascii_case("dev_owned") => false,
        Ok(value) if value.eq_ignore_ascii_case("dev_external") => false,
        _ => cfg!(not(debug_assertions)),
    }
}

fn sidecar_candidates() -> Vec<PathBuf> {
    let mut out = Vec::new();
    if let Ok(raw) = std::env::var("ALEX_BACKEND_SIDECAR") {
        if !raw.trim().is_empty() {
            out.push(PathBuf::from(raw));
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            out.push(dir.join("alex-backend").join("alex-backend.exe"));
            out.push(dir.join("sidecar").join("alex-backend").join("alex-backend.exe"));
            out.push(dir.join("resources").join("alex-backend").join("alex-backend.exe"));
            out.push(
                dir.join("resources")
                    .join("sidecar")
                    .join("alex-backend")
                    .join("alex-backend.exe"),
            );
            out.push(dir.join("alex-backend.exe"));
        }
    }
    out
}

fn find_sidecar_exe() -> Option<PathBuf> {
    sidecar_candidates().into_iter().find(|path| path.is_file())
}

#[allow(dead_code)]
fn native_host_candidates() -> Vec<PathBuf> {
    let mut out = Vec::new();
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            out.push(dir.join("alex-host-loop.exe"));
            out.push(dir.join("sidecar").join("alex-host-loop.exe"));
            out.push(dir.join("resources").join("alex-host-loop.exe"));
        }
    }
    out
}

#[allow(dead_code)]
pub fn find_native_host() -> Option<PathBuf> {
    native_host_candidates().into_iter().find(|path| path.is_file())
}

fn discover_backend_launch() -> Result<(PathBuf, PathBuf, RuntimeMode), String> {
    if let Some(exe) = find_sidecar_exe() {
        let cwd = exe.parent().map(Path::to_path_buf).unwrap_or_else(|| PathBuf::from("."));
        return Ok((exe, cwd, RuntimeMode::Packaged));
    }
    if packaged_required() {
        return Err("BACKEND_SIDECAR_MISSING".into());
    }
    let (python, cwd) = discover_backend_python()?;
    Ok((python, cwd, RuntimeMode::DevOwned))
}

fn discover_backend_python() -> Result<(PathBuf, PathBuf), String> {
    if let Ok(raw) = std::env::var("ALEX_BACKEND_PYTHON") {
        let python = PathBuf::from(raw);
        if python.is_file() {
            if let Some(cwd) = find_backend_cwd(&python) {
                return Ok((python, cwd));
            }
        }
    }
    let mut roots = Vec::new();
    if let Ok(exe) = std::env::current_exe() {
        if let Some(parent) = exe.parent() {
            roots.push(parent.to_path_buf());
        }
    }
    if let Ok(cwd) = std::env::current_dir() {
        roots.push(cwd);
    }
    for mut dir in roots {
        for _ in 0..10 {
            for rel in [
                "apps/backend/.venv/Scripts/python.exe",
                "backend/.venv/Scripts/python.exe",
            ] {
                let python = dir.join(rel);
                if python.is_file() {
                    if let Some(cwd) = find_backend_cwd(&python) {
                        return Ok((python, cwd));
                    }
                }
            }
            match dir.parent() {
                Some(parent) => dir = parent.to_path_buf(),
                None => break,
            }
        }
    }
    Err("BACKEND_START_FAILED".into())
}

fn find_backend_cwd(python: &Path) -> Option<PathBuf> {
    let mut dir = python.parent().map(Path::to_path_buf);
    for _ in 0..6 {
        let Some(current) = dir.clone() else { break };
        if current.join("alembic.ini").is_file() && current.join("app").is_dir() {
            return Some(current);
        }
        dir = current.parent().map(Path::to_path_buf);
    }
    None
}

fn create_job() -> Result<OwnedJob, String> {
    use std::os::windows::io::{FromRawHandle, OwnedHandle};
    use windows::Win32::System::JobObjects::{
        CreateJobObjectW, JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };
    unsafe {
        let job = CreateJobObjectW(None, windows::core::PCWSTR::null()).map_err(|e| e.to_string())?;
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
    use std::os::windows::io::AsRawHandle;
    use windows::Win32::Foundation::HANDLE;
    use windows::Win32::System::JobObjects::AssignProcessToJobObject;
    unsafe {
        AssignProcessToJobObject(HANDLE(job.0.as_raw_handle()), HANDLE(child.as_raw_handle()))
            .map_err(|e| e.to_string())
    }
}

fn process_alive(pid: u32) -> bool {
    use windows::Win32::Foundation::{CloseHandle, STILL_ACTIVE};
    use windows::Win32::System::Threading::{OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION, GetExitCodeProcess};
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

fn terminate_pid(pid: u32) {
    use windows::Win32::Foundation::CloseHandle;
    use windows::Win32::System::Threading::{OpenProcess, PROCESS_TERMINATE, TerminateProcess};
    unsafe {
        if let Ok(handle) = OpenProcess(PROCESS_TERMINATE, false, pid) {
            let _ = TerminateProcess(handle, 1);
            let _ = CloseHandle(handle);
        }
    }
}

fn write_lock(root: &Path, pid: u32, port: u16, instance: &str, owned: bool) {
    let path = root.join("runtime").join("backend.lock");
    let _ = fs::write(
        path,
        serde_json::to_vec(&json!({
            "pid": pid,
            "port": port,
            "instance": instance,
            "owned": owned,
        }))
        .unwrap_or_default(),
    );
}

fn read_lock(root: &Path) -> Option<Value> {
    serde_json::from_slice(&fs::read(root.join("runtime").join("backend.lock")).ok()?).ok()
}

fn spawn_owned(port: u16, instance: &str, secret: &str) -> Result<Live, String> {
    let root = data_root();
    let (data, documents, logs, _runtime, _models) = layout(&root)?;
    let (program, cwd, mode) = discover_backend_launch()?;
    let log_path = logs.join("backend.log");
    let log = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
        .map_err(|_| "DATA_ROOT_UNAVAILABLE".to_string())?;
    let err = log.try_clone().map_err(|_| "DATA_ROOT_UNAVAILABLE".to_string())?;
    let db = data.join("alex.db");
    let token = load_or_create_runtime_token(&root).unwrap_or_default();
    let mut command = Command::new(&program);
    if mode != RuntimeMode::Packaged {
        command.args(["-m", "app.runtime_entry"]);
    }
    command
        .current_dir(&cwd)
        .env("ALEX_LLM_DATA_DIR", &root)
        .env("DATABASE_URL", sqlite_url(&db))
        .env("DOCUMENT_STORAGE_DIR", &documents)
        .env("JWT_SECRET", secret)
        .env("ALEX_RUNTIME_TOKEN", &token)
        .env("ALEX_BACKEND_PORT", port.to_string())
        .env("ALEX_BACKEND_HOST", "127.0.0.1")
        .env("ALEX_BACKEND_INSTANCE", instance)
        .env("ALEX_BACKEND_OWNED", "1")
        .env(
            "APP_ENV",
            if mode == RuntimeMode::Packaged {
                "production"
            } else {
                "development"
            },
        )
        .stdin(Stdio::null())
        .stdout(Stdio::from(log))
        .stderr(Stdio::from(err));
    if mode == RuntimeMode::Packaged {
        command.env("ALEX_PACKAGED", "1");
        command.env("CORS_ORIGINS", r#"["https://tauri.localhost","tauri://localhost"]"#);
        command.env("ALEX_RUNTIME_MODE", "packaged");
        command.env("LLM_PROVIDER", "llamacpp");
        command.env("LLM_CONNECTION_MODE", "runpod");
    } else {
        command.env("ALEX_RUNTIME_MODE", "dev_owned");
    }
    // User-configured provider secrets (Credential Manager) win over stale
    // .env entries; absent credentials leave the developer .env untouched.
    for (name, value) in crate::auth::provider_env() {
        command.env(name, value);
    }
    // Alex Cloud: when the installation is enrolled, the backend runs in production
    // shared mode. The installation credential is a client credential (it authorizes
    // this installation), while the RunPod master key stays server-side.
    for (name, value) in crate::gateway::gateway_env() {
        command.env(name, value);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        const CREATE_UNICODE_ENVIRONMENT: u32 = 0x0000_0400;
        const CREATE_BREAKAWAY_FROM_JOB: u32 = 0x0100_0000;
        command.creation_flags(CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT | CREATE_BREAKAWAY_FROM_JOB);
    }
    command.env("PYTHONUNBUFFERED", "1");
    let child = match command.spawn() {
        Ok(child) => child,
        Err(_) => {
            #[cfg(windows)]
            {
                use std::os::windows::process::CommandExt;
                const CREATE_NO_WINDOW: u32 = 0x0800_0000;
                const CREATE_UNICODE_ENVIRONMENT: u32 = 0x0000_0400;
                command.creation_flags(CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT);
            }
            command
                .spawn()
                .map_err(|_| "BACKEND_START_FAILED".to_string())?
        }
    };
    let job = create_job().ok().and_then(|job| {
        if assign_job(&job, &child).is_ok() {
            Some(job)
        } else {
            None
        }
    });
    let pid = child.id();
    write_lock(&root, pid, port, instance, true);
    Ok(Live {
        child: Some(child),
        job,
        pid,
        port,
        instance: instance.to_string(),
        ownership: Ownership::Owned,
        mode,
        restarts: 0,
    })
}

fn protocol_compatible(body: &Value) -> bool {
    match body.get("runtime_protocol_version").and_then(Value::as_u64) {
        None => true,
        Some(value) => value == EXPECTED_PROTOCOL,
    }
}

fn wait_ready(port: u16, instance: &str, timeout: Duration) -> Result<Value, String> {
    let url = url_for(port);
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if let Some(body) = alex_health(&url) {
            if body.get("instance").and_then(Value::as_str) == Some(instance)
                || body.get("instance").and_then(Value::as_str).is_none()
            {
                return Ok(body);
            }
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    Err("BACKEND_HEALTH_TIMEOUT".into())
}

fn status_from_live(live: &Live, state: BackendState, error: Option<String>) -> BackendStatus {
    BackendStatus {
        state,
        ownership: live.ownership.clone(),
        url: Some(url_for(live.port)),
        port: Some(live.port),
        pid: Some(live.pid),
        error,
        data_dir: data_root().to_string_lossy().into_owned(),
        runtime_mode: live.mode.clone(),
        diagnostic: None,
    }
}

fn connect_existing(port: u16, body: &Value, root: &Path) -> Live {
    let lock = read_lock(root);
    let instance = body.get("instance").and_then(Value::as_str).unwrap_or("");
    let lock_instance = lock
        .as_ref()
        .and_then(|row| row.get("instance"))
        .and_then(Value::as_str)
        .unwrap_or("");
    let lock_owned = lock
        .as_ref()
        .and_then(|row| row.get("owned"))
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let pid = lock
        .as_ref()
        .and_then(|row| row.get("pid"))
        .and_then(Value::as_u64)
        .map(|n| n as u32)
        .filter(|pid| process_alive(*pid))
        .unwrap_or(0);
    let owned = lock_owned && !instance.is_empty() && instance == lock_instance && pid != 0;
    Live {
        child: None,
        job: None,
        pid,
        port,
        instance: instance.to_string(),
        ownership: if owned {
            Ownership::Owned
        } else {
            Ownership::External
        },
        mode: RuntimeMode::DevExternal,
        restarts: 0,
    }
}

fn spawn_and_wait(sup: &mut Supervisor, port: u16, restarts: u8) -> Result<BackendStatus, String> {
    let root = data_root();
    let instance = UuidLite::instance();
    let secret = load_or_create_jwt(&root)?;
    if secret.contains('\n') || secret.len() < 48 {
        return Err("JWT_SECRET_FAILED".into());
    }
    let mut live = spawn_owned(port, &instance, &secret)?;
    live.restarts = restarts;
    let timeout = if live.mode == RuntimeMode::Packaged {
        Duration::from_secs(90)
    } else {
        STARTUP_TIMEOUT
    };
    match wait_ready(port, &instance, timeout) {
        Ok(body) => {
            if live.mode == RuntimeMode::Packaged && !protocol_compatible(&body) {
                if let Some(mut child) = live.child.take() {
                    let _ = child.kill();
                    let _ = child.wait();
                }
                return Err("BACKEND_VERSION_MISMATCH".into());
            }
            let status = status_from_live(&live, BackendState::Ready, None);
            sup.live = Some(live);
            sup.last = status.clone();
            Ok(status)
        }
        Err(code) => {
            if let Some(mut child) = live.child.take() {
                let _ = child.kill();
                let wait = child.wait().ok().and_then(|status| status.code());
                let mapped = if wait == Some(12) {
                    "MIGRATION_FAILED"
                } else if wait == Some(13) {
                    "JWT_SECRET_FAILED"
                } else if wait == Some(14) {
                    "DATA_ROOT_UNAVAILABLE"
                } else {
                    &code
                };
                let status = BackendStatus::error(mapped, root);
                sup.live = None;
                sup.last = status.clone();
                Ok(status)
            } else {
                let status = BackendStatus::error(&code, root);
                sup.live = None;
                sup.last = status.clone();
                Ok(status)
            }
        }
    }
}

fn ensure_locked(sup: &mut Supervisor) -> Result<BackendStatus, String> {
    let root = data_root();
    layout(&root)?;
    let mut restart_from = 0u8;
    if let Some(live) = sup.live.as_mut() {
        if live.ownership == Ownership::Owned {
            if let Some(child) = live.child.as_mut() {
                match child.try_wait() {
                    Ok(None) => {
                        if alex_health(&url_for(live.port)).is_some() {
                            let status = status_from_live(live, BackendState::Ready, None);
                            sup.last = status.clone();
                            return Ok(status);
                        }
                        let status = status_from_live(live, BackendState::Starting, None);
                        sup.last = status.clone();
                        return Ok(status);
                    }
                    Ok(Some(_)) => {
                        if live.restarts >= 1 {
                            let failed = BackendStatus::error("BACKEND_START_FAILED", root.clone());
                            sup.live = None;
                            sup.last = failed.clone();
                            return Ok(failed);
                        }
                        restart_from = live.restarts + 1;
                        sup.live = None;
                    }
                    Err(_) => {}
                }
            } else if process_alive(live.pid) && alex_health(&url_for(live.port)).is_some() {
                let status = status_from_live(live, BackendState::Ready, None);
                sup.last = status.clone();
                return Ok(status);
            } else {
                sup.live = None;
            }
        } else if live.ownership == Ownership::External {
            if alex_health(&url_for(live.port)).is_some() {
                let status = status_from_live(live, BackendState::Ready, None);
                sup.last = status.clone();
                return Ok(status);
            }
            sup.live = None;
        }
    }

    let port = select_listen_port(PREFERRED_PORT)?;
    match classify_port(port) {
        PortKind::Alex => {
            let body = alex_health(&url_for(port)).unwrap_or(json!({}));
            let live = connect_existing(port, &body, &root);
            let mut status = status_from_live(&live, BackendState::Ready, None);
            if !protocol_compatible(&body) {
                status.diagnostic = Some("BACKEND_VERSION_MISMATCH".into());
            }
            sup.live = Some(live);
            sup.last = status.clone();
            Ok(status)
        }
        PortKind::Unrelated => Err("NO_SAFE_BACKEND_PORT".into()),
        PortKind::Free => spawn_and_wait(sup, port, restart_from),
    }
}

pub fn ensure_backend_blocking() -> Result<BackendStatus, String> {
    let mut sup = supervisor().lock().map_err(|e| e.to_string())?;
    match ensure_locked(&mut sup) {
        Ok(status) => Ok(status),
        Err(code) => {
            let status = BackendStatus::error(&code, data_root());
            sup.last = status.clone();
            Ok(status)
        }
    }
}

pub fn current_status() -> BackendStatus {
    supervisor()
        .lock()
        .map(|sup| sup.last.clone())
        .unwrap_or_else(|_| BackendStatus::error("supervisor_lock", data_root()))
}

pub fn restart_backend_blocking() -> Result<BackendStatus, String> {
    let mut sup = supervisor().lock().map_err(|e| e.to_string())?;
    let root = data_root();
    if let Some(mut live) = sup.live.take() {
        if live.ownership != Ownership::Owned {
            // An external developer backend is not ours to restart.
            sup.live = Some(live);
            return Ok(sup.last.clone());
        }
        // Same managed-compute stop as full Quit: never leave a billed Pod
        // running while the supervising backend is replaced.
        let port = live.port;
        let pid = live.pid;
        let had_job = live.job.is_some();
        let token = fs::read_to_string(root.join("runtime").join("shutdown.token"))
            .unwrap_or_default();
        request_managed_shutdown(port, token.trim());
        if let Some(mut child) = live.child.take() {
            let _ = child.kill();
            let _ = child.wait();
        } else if pid != 0 {
            terminate_pid(pid);
        }
        // Dropping the Job Object stops the sidecar tree (KILL_ON_JOB_CLOSE).
        drop(live);
        if !had_job && pid != 0 {
            terminate_tree(pid);
        }
        let _ = fs::remove_file(root.join("runtime").join("backend.lock"));
        // Wait for the port to free so ensure_locked spawns a fresh backend
        // instead of adopting the dying one as external.
        let deadline = Instant::now() + Duration::from_secs(10);
        while Instant::now() < deadline && port_connectable(port) {
            std::thread::sleep(Duration::from_millis(150));
        }
        if port_connectable(port) {
            let status = BackendStatus::error("BACKEND_STOP_TIMEOUT", root);
            sup.last = status.clone();
            return Ok(status);
        }
    }
    match ensure_locked(&mut sup) {
        Ok(status) => Ok(status),
        Err(code) => {
            let status = BackendStatus::error(&code, root);
            sup.last = status.clone();
            Ok(status)
        }
    }
}

#[cfg(windows)]
fn terminate_tree(pid: u32) {
    // Terminates only the process tree rooted at our own spawned PID.
    let _ = std::process::Command::new("taskkill")
        .args(["/PID", &pid.to_string(), "/T", "/F"])
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status();
}

#[cfg(not(windows))]
fn terminate_tree(_pid: u32) {}

pub fn on_desktop_exit() {
    // Full application Quit only (Tauri Exit / ExitRequested). In-app window
    // navigation does not run this. External backends are left running.
    let Ok(mut sup) = supervisor().lock() else {
        return;
    };
    if let Some(mut live) = sup.live.take() {
        if live.ownership != Ownership::Owned {
            return;
        }
        let token = fs::read_to_string(data_root().join("runtime").join("shutdown.token"))
            .unwrap_or_default();
        request_managed_shutdown(live.port, token.trim());
        let had_job = live.job.is_some();
        if let Some(mut child) = live.child.take() {
            let _ = child.kill();
            let _ = child.wait();
        } else if live.pid != 0 {
            terminate_pid(live.pid);
        }
        if !had_job && live.pid != 0 {
            // Without a Job Object the PyInstaller sidecar tree must be
            // stopped explicitly, or the grandchild would leak.
            terminate_tree(live.pid);
        }
        drop(live);
        let lock = data_root().join("runtime").join("backend.lock");
        let _ = fs::remove_file(lock);
    }
}

#[tauri::command]
pub async fn ensure_backend() -> Result<BackendStatus, String> {
    tauri::async_runtime::spawn_blocking(ensure_backend_blocking)
        .await
        .map_err(|error| error.to_string())?
}

#[tauri::command]
pub fn backend_status() -> BackendStatus {
    current_status()
}

#[tauri::command]
pub async fn restart_backend() -> Result<BackendStatus, String> {
    tauri::async_runtime::spawn_blocking(restart_backend_blocking)
        .await
        .map_err(|error| error.to_string())?
}

pub(crate) mod uuid_lite {
    use sha2::{Digest, Sha256};
    use std::time::{SystemTime, UNIX_EPOCH};

    pub struct UuidLite;
    impl UuidLite {
        fn bytes() -> String {
            let nanos = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map(|d| d.as_nanos())
                .unwrap_or(0);
            let mut hasher = Sha256::new();
            hasher.update(nanos.to_le_bytes());
            hasher.update(format!("{nanos}").as_bytes());
            format!("{:x}", hasher.finalize())
        }
        pub fn instance() -> String {
            Self::bytes()[..32].to_string()
        }
        pub fn secret() -> String {
            Self::bytes() + &Self::bytes()
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;
    use std::path::PathBuf;
    use std::process::Command;
    use std::thread;

    #[test]
    fn data_root_ignores_cwd() {
        let _guard = crate::credential::TEST_ENV_LOCK.lock().unwrap();
        let previous = std::env::var("ALEX_LLM_DATA_DIR").ok();
        let temp = std::env::temp_dir().join(format!("alex-runtime-{}", UuidLite::instance()));
        std::env::set_var("ALEX_LLM_DATA_DIR", &temp);
        let original = std::env::current_dir().unwrap();
        let other = temp.join("elsewhere");
        let _ = fs::create_dir_all(&other);
        std::env::set_current_dir(&other).unwrap();
        let root = data_root();
        std::env::set_current_dir(original).unwrap();
        match previous {
            Some(value) => std::env::set_var("ALEX_LLM_DATA_DIR", value),
            None => std::env::remove_var("ALEX_LLM_DATA_DIR"),
        }
        assert_eq!(root, temp);
        assert!(!root.ends_with("elsewhere"));
    }

    #[test]
    fn jwt_is_stable_and_redacted() {
        let temp = std::env::temp_dir().join(format!("alex-jwt-{}", UuidLite::instance()));
        let _ = fs::create_dir_all(temp.join("runtime"));
        let first = load_or_create_jwt(&temp).unwrap();
        let second = load_or_create_jwt(&temp).unwrap();
        assert_eq!(first, second);
        assert!(first.len() >= 48);
        let leaked = format!("started with {first}");
        assert!(!redact_secret(&leaked, &first).contains(&first));
        assert!(redact_secret(&leaked, &first).contains("[redacted]"));
    }

    #[test]
    fn unrelated_occupant_is_not_classified_as_alex() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        thread::spawn(move || {
            let _keep = listener;
            thread::sleep(Duration::from_secs(2));
        });
        thread::sleep(Duration::from_millis(50));
        assert_eq!(classify_port(port), PortKind::Unrelated);
    }

    #[test]
    fn select_port_skips_unrelated_without_killing() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let occupied = listener.local_addr().unwrap().port();
        thread::spawn(move || {
            let _keep = listener;
            thread::sleep(Duration::from_secs(3));
        });
        thread::sleep(Duration::from_millis(50));
        let chosen = select_listen_port(occupied).unwrap();
        assert_ne!(chosen, occupied);
        assert!(TcpStream::connect_timeout(
            &format!("127.0.0.1:{occupied}").parse().unwrap(),
            Duration::from_millis(200),
        )
        .is_ok());
    }

    #[test]
    fn free_port_is_free() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        drop(listener);
        assert_eq!(classify_port(port), PortKind::Free);
    }

    fn python_bin() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../backend/.venv/Scripts/python.exe")
    }

    #[test]
    fn quit_stops_owned_child_and_leaves_external() {
        let python = python_bin();
        let owned = Command::new(&python)
            .args(["-c", "import time; time.sleep(30)"])
            .spawn()
            .unwrap();
        let owned_pid = owned.id();
        {
            let mut sup = supervisor().lock().unwrap();
            sup.live = Some(Live {
                child: Some(owned),
                job: None,
                pid: owned_pid,
                port: 1,
                instance: "owned".into(),
                ownership: Ownership::Owned,
                mode: RuntimeMode::DevOwned,
                restarts: 0,
            });
        }
        on_desktop_exit();
        thread::sleep(Duration::from_millis(200));
        assert!(!process_alive(owned_pid));

        let mut external = Command::new(&python)
            .args(["-c", "import time; time.sleep(30)"])
            .spawn()
            .unwrap();
        let external_pid = external.id();
        {
            let mut sup = supervisor().lock().unwrap();
            sup.live = Some(Live {
                child: None,
                job: None,
                pid: external_pid,
                port: 1,
                instance: "ext".into(),
                ownership: Ownership::External,
                mode: RuntimeMode::DevExternal,
                restarts: 0,
            });
        }
        on_desktop_exit();
        thread::sleep(Duration::from_millis(200));
        assert!(process_alive(external_pid));
        let _ = external.kill();
        let _ = external.wait();
    }

    #[test]
    fn packaged_mode_does_not_search_python() {
        let _guard = crate::credential::TEST_ENV_LOCK.lock().unwrap();
        let mode = std::env::var("ALEX_RUNTIME_MODE").ok();
        let sidecar = std::env::var("ALEX_BACKEND_SIDECAR").ok();
        std::env::set_var("ALEX_RUNTIME_MODE", "packaged");
        std::env::set_var("ALEX_BACKEND_SIDECAR", "");
        let result = discover_backend_launch();
        match mode {
            Some(value) => std::env::set_var("ALEX_RUNTIME_MODE", value),
            None => std::env::remove_var("ALEX_RUNTIME_MODE"),
        }
        match sidecar {
            Some(value) => std::env::set_var("ALEX_BACKEND_SIDECAR", value),
            None => std::env::remove_var("ALEX_BACKEND_SIDECAR"),
        }
        assert_eq!(result.unwrap_err(), "BACKEND_SIDECAR_MISSING");
    }

    #[test]
    fn protocol_accepts_missing_or_matching() {
        assert!(protocol_compatible(&json!({"product": "alex-llm"})));
        assert!(protocol_compatible(&json!({"runtime_protocol_version": 1})));
        assert!(!protocol_compatible(&json!({"runtime_protocol_version": 99})));
    }

    #[test]
    fn native_host_search_does_not_require_repo() {
        let found = find_native_host();
        if let Some(path) = found {
            assert!(path.ends_with("alex-host-loop.exe"));
        }
    }
}
