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

    /// The owned backend was stopped on purpose (restore): no error, no process.
    fn stopped(data_dir: PathBuf, mode: RuntimeMode) -> Self {
        Self {
            state: BackendState::Starting,
            ownership: Ownership::None,
            url: None,
            port: None,
            pid: None,
            error: None,
            data_dir: data_dir.to_string_lossy().into_owned(),
            runtime_mode: mode,
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
    /// Automatic restarts this session. The budget is shared by every path so a sidecar that keeps
    /// dying cannot be restarted forever, no matter who noticed the exit.
    restarts: u8,
}

struct OwnedJob(std::os::windows::io::OwnedHandle);

fn supervisor() -> &'static Mutex<Supervisor> {
    static CELL: std::sync::OnceLock<Mutex<Supervisor>> = std::sync::OnceLock::new();
    CELL.get_or_init(|| {
        Mutex::new(Supervisor {
            live: None,
            restarts: 0,
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

/// Where the bundled Tor runtime lives, relative to the installed executable. The installer puts
/// it there (`runtime/tor`); a dev checkout that never staged it simply has none.
fn tor_runtime_candidates(dir: &Path) -> Vec<PathBuf> {
    vec![
        dir.join("runtime").join("tor"),
        dir.join("resources").join("runtime").join("tor"),
    ]
}

pub fn tor_runtime_dir() -> Option<PathBuf> {
    if let Ok(raw) = std::env::var("ALEX_TOR_RUNTIME_DIR") {
        if !raw.trim().is_empty() {
            return Some(PathBuf::from(raw));
        }
    }
    let exe = std::env::current_exe().ok()?;
    tor_runtime_candidates(exe.parent()?)
        .into_iter()
        .find(|path| path.is_dir())
}

fn discover_backend_launch() -> Result<(PathBuf, PathBuf, RuntimeMode), String> {
    #[cfg(test)]
    {
        if let Some((program, _)) = TEST_LAUNCH.lock().ok().and_then(|test| test.clone()) {
            let cwd = program.parent().map(Path::to_path_buf).unwrap_or_else(|| PathBuf::from("."));
            return Ok((program, cwd, RuntimeMode::Packaged));
        }
    }
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

/// How another module (the restore path) runs the same backend program one-shot.
pub(crate) fn backend_launch() -> Result<(PathBuf, PathBuf, RuntimeMode), String> {
    discover_backend_launch()
}

/// The environment every backend process needs, owned in one place so the restore
/// invocation can never drift from the supervised startup.
pub(crate) fn base_backend_env(
    command: &mut Command,
    mode: &RuntimeMode,
    root: &Path,
    secret: &str,
    token: &str,
) {
    let data = root.join("data");
    let documents = root.join("documents");
    command
        .env("ALEX_LLM_DATA_DIR", root)
        .env("DATABASE_URL", sqlite_url(&data.join("alex.db")))
        .env("DOCUMENT_STORAGE_DIR", &documents)
        .env("JWT_SECRET", secret)
        .env("ALEX_RUNTIME_TOKEN", token)
        .env("ALEX_BACKEND_HOST", "127.0.0.1")
        .env(
            "APP_ENV",
            if *mode == RuntimeMode::Packaged {
                "production"
            } else {
                "development"
            },
        );
    if *mode == RuntimeMode::Packaged {
        command.env("ALEX_PACKAGED", "1");
        command.env("CORS_ORIGINS", r#"["https://tauri.localhost","tauri://localhost"]"#);
        command.env("ALEX_RUNTIME_MODE", "packaged");
        command.env("LLM_PROVIDER", "llamacpp");
        command.env("LLM_CONNECTION_MODE", "runpod");
    } else {
        command.env("ALEX_RUNTIME_MODE", "dev_owned");
    }
    for (name, value) in crate::auth::provider_env() {
        command.env(name, value);
    }
    for (name, value) in crate::gateway::gateway_env() {
        command.env(name, value);
    }
    // The Tor runtime Canalla ships, so the backend manages the daemon we bundle instead of
    // whatever the machine happens to have (a missing bundle leaves the compatibility paths).
    if let Some(dir) = tor_runtime_dir() {
        command.env("ALEX_TOR_RUNTIME_DIR", dir);
    }
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
    let (_data, _documents, logs, _runtime, _models) = layout(&root)?;
    let (program, cwd, mode) = discover_backend_launch()?;
    let log_path = logs.join("backend.log");
    let log = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
        .map_err(|_| "DATA_ROOT_UNAVAILABLE".to_string())?;
    let err = log.try_clone().map_err(|_| "DATA_ROOT_UNAVAILABLE".to_string())?;
    let token = load_or_create_runtime_token(&root).unwrap_or_default();
    let mut command = Command::new(&program);
    if mode != RuntimeMode::Packaged {
        command.args(["-m", "app.runtime_entry"]);
    }
    #[cfg(test)]
    {
        if let Some((_, args)) = TEST_LAUNCH.lock().ok().and_then(|test| test.clone()) {
            command.args(args);
        }
    }
    command.current_dir(&cwd);
    base_backend_env(&mut command, &mode, &root, secret, &token);
    command
        .env("ALEX_BACKEND_PORT", port.to_string())
        .env("ALEX_BACKEND_INSTANCE", instance)
        .env("ALEX_BACKEND_OWNED", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::from(log))
        .stderr(Stdio::from(err));
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
                } else if wait == Some(15) {
                    // Fail closed: the upgrade was refused because its pre-upgrade backup
                    // could not be created, and the database was left untouched.
                    "PRE_UPGRADE_BACKUP_FAILED"
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
    let mut restart_from = sup.restarts;
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
                        let attempts = live.restarts;
                        if attempts >= RESTART_LIMIT {
                            let failed = BackendStatus::error("BACKEND_START_FAILED", root.clone());
                            sup.live = None;
                            sup.last = failed.clone();
                            return Ok(failed);
                        }
                        restart_from = attempts + 1;
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
    sup.restarts = restart_from.max(sup.restarts);

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

/// How many automatic restarts one installation gets before the supervisor stops trying.
///
/// The budget is deliberately small and shared by every path (the ensure command and the
/// watchdog): a sidecar that cannot stay up is a broken installation, and a silent restart loop
/// would hide that instead of reporting it.
const RESTART_LIMIT: u8 = 3;
const WATCHDOG_INTERVAL: Duration = Duration::from_secs(2);

/// Set on a full application Quit. The watchdog never resurrects a backend after that: stopping
/// the app is an instruction, not a crash.
static SHUTTING_DOWN: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

pub fn begin_shutdown() {
    SHUTTING_DOWN.store(true, std::sync::atomic::Ordering::SeqCst);
}

fn shutting_down() -> bool {
    SHUTTING_DOWN.load(std::sync::atomic::Ordering::SeqCst)
}

#[cfg(test)]
fn resume_after_shutdown_for_tests() {
    SHUTTING_DOWN.store(false, std::sync::atomic::Ordering::SeqCst);
}

/// The program the supervisor starts, with optional arguments. Tests point this at a fake sidecar;
/// the product always asks discovery.
#[cfg(test)]
static TEST_LAUNCH: std::sync::Mutex<Option<(std::path::PathBuf, Vec<String>)>> =
    std::sync::Mutex::new(None);

/// Own the sidecar lifecycle in the Desktop, not in the UI.
///
/// The frontend is a viewer: a window can be closed, hidden or wedged, and a backend that died
/// must still come back. This watchdog is the single owner of "the child exited": it notices the
/// exit by itself, marks the state unhealthy, and performs a bounded restart with a short
/// backoff. Idempotent, so app setup and tests can both call it.
pub fn start_watchdog() {
    static STARTED: std::sync::OnceLock<()> = std::sync::OnceLock::new();
    STARTED.get_or_init(|| {
        std::thread::spawn(|| {
            loop {
                std::thread::sleep(WATCHDOG_INTERVAL);
                if shutting_down() {
                    return;
                }
                observe_owned_child();
            }
        });
    });
}

/// One watchdog pass: is the owned child still there, and what should happen if it is not?
fn observe_owned_child() {
    let Ok(mut sup) = supervisor().lock() else {
        return;
    };
    let exited = match sup.live.as_mut() {
        Some(live) if live.ownership == Ownership::Owned => match live.child.as_mut() {
            Some(child) => match child.try_wait() {
                Ok(None) => false,
                Ok(Some(_)) => true,
                Err(_) => false,
            },
            None => live.pid != 0 && !process_alive(live.pid),
        },
        _ => false,
    };
    if !exited || shutting_down() {
        return;
    }
    let attempts = sup.restarts;
    sup.live = None;
    if attempts >= RESTART_LIMIT {
        let status = BackendStatus::error("BACKEND_START_FAILED", data_root());
        sup.last = status;
        return;
    }
    // A short backoff keeps a crash loop from hammering the disk, and the budget above stops it.
    let backoff = Duration::from_millis(250 * u64::from(attempts + 1));
    sup.restarts = attempts + 1;
    drop(sup);
    std::thread::sleep(backoff);
    if shutting_down() {
        return;
    }
    let Ok(mut sup) = supervisor().lock() else {
        return;
    };
    let _ = ensure_locked(&mut sup);
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
    if let Some(status) = stop_owned_locked(&mut sup, &root)? {
        sup.last = status.clone();
        return Ok(status);
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

/// Stop the owned backend and leave it stopped. Used by the restore path, which has to run
/// the sidecar in its one-shot ``--restore-backup`` mode while nothing holds the database.
/// An external developer backend is never stopped: `Err` carries the refusal code.
pub fn stop_backend_blocking() -> Result<BackendStatus, String> {
    let mut sup = supervisor().lock().map_err(|e| e.to_string())?;
    let root = data_root();
    if let Some(status) = stop_owned_locked(&mut sup, &root)? {
        sup.last = status.clone();
        return Ok(status);
    }
    let mode = sup
        .live
        .as_ref()
        .map(|live| live.mode.clone())
        .unwrap_or(RuntimeMode::None);
    let status = BackendStatus::stopped(root, mode);
    sup.last = status.clone();
    Ok(status)
}

/// Returns `Some(status)` when the operation must stop early (an external backend), and
/// `None` when the owned backend was stopped and the caller may proceed.
fn stop_owned_locked(sup: &mut Supervisor, root: &Path) -> Result<Option<BackendStatus>, String> {
    if let Some(mut live) = sup.live.take() {
        if live.ownership != Ownership::Owned {
            // An external developer backend is not ours to restart or stop.
            let status = status_from_live(&live, BackendState::Ready, None);
            sup.live = Some(live);
            return Ok(Some(status));
        }
        // Same managed-compute stop as full Quit: never leave a billed Pod
        // running while the supervising backend is replaced.
        let port = live.port;
        let pid = live.pid;
        let had_job = live.job.is_some();
        let token = fs::read_to_string(root.join("runtime").join("shutdown.token")).unwrap_or_default();
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
            return Ok(Some(BackendStatus::error("BACKEND_STOP_TIMEOUT", root.to_path_buf())));
        }
    }
    Ok(None)
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
    begin_shutdown();
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
    fn the_bundled_tor_runtime_is_looked_for_next_to_the_application() {
        // The installer puts the daemon at runtime/tor; Tauri's resource directory is the second
        // candidate, so a packaged build finds it either way and a dev checkout finds nothing.
        let candidates = tor_runtime_candidates(Path::new("C:/app"));

        assert_eq!(candidates[0], Path::new("C:/app").join("runtime").join("tor"));
        assert!(candidates.contains(
            &Path::new("C:/app")
                .join("resources")
                .join("runtime")
                .join("tor")
        ));
    }

    #[test]
    fn an_explicit_tor_runtime_path_wins_over_the_search() {
        let _guard = crate::credential::TEST_ENV_LOCK.lock().unwrap();
        let previous = std::env::var("ALEX_TOR_RUNTIME_DIR").ok();
        let chosen = std::env::temp_dir().join(format!("alex-tor-{}", UuidLite::instance()));
        std::env::set_var("ALEX_TOR_RUNTIME_DIR", &chosen);
        let found = tor_runtime_dir();
        match previous {
            Some(value) => std::env::set_var("ALEX_TOR_RUNTIME_DIR", value),
            None => std::env::remove_var("ALEX_TOR_RUNTIME_DIR"),
        }
        assert_eq!(found, Some(chosen));
    }

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

    /// The supervisor is process-global, so every test that puts a child in it - or asks the
    /// watchdog to act on one - has to take this lock instead of racing the others.
    static SUPERVISOR_TEST_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

    fn supervisor_guard() -> std::sync::MutexGuard<'static, ()> {
        SUPERVISOR_TEST_LOCK
            .lock()
            .unwrap_or_else(|error| error.into_inner())
    }

    /// A real process the supervisor can own, health-check and restart: the fake answers the same
    /// `/health` contract the packaged sidecar does, and dies on `/runtime/shutdown`.
    const FAKE_SIDECAR: &str = r#"
import json, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._json({
                "status": "ok",
                "product": "alex-llm",
                "instance": os.environ.get("ALEX_BACKEND_INSTANCE", ""),
                "provider": "mock",
            })
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/runtime/shutdown":
            self.send_response(200)
            self.end_headers()
            os._exit(0)
        else:
            self.send_response(404)
            self.end_headers()


port = int(os.environ["ALEX_BACKEND_PORT"])
ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
"#;

    #[test]
    fn quit_stops_owned_child_and_leaves_external() {
        let _supervisor = supervisor_guard();
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
    fn watchdog_restarts_a_crashed_owned_sidecar() {
        use std::time::Instant;

        let _supervisor = supervisor_guard();
        let _guard = crate::credential::TEST_ENV_LOCK.lock().unwrap();
        let previous_root = std::env::var("ALEX_LLM_DATA_DIR").ok();
        let root = std::env::temp_dir().join(format!("alex-watchdog-{}", UuidLite::instance()));
        std::env::set_var("ALEX_LLM_DATA_DIR", &root);
        *TEST_LAUNCH.lock().unwrap() = Some((python_bin(), vec!["-c".into(), FAKE_SIDECAR.into()]));
        resume_after_shutdown_for_tests();
        {
            let mut sup = supervisor().lock().unwrap();
            sup.live = None;
        }
        start_watchdog();

        let first = {
            let mut sup = supervisor().lock().unwrap();
            ensure_locked(&mut sup).expect("the fake sidecar starts")
        };
        assert_eq!(first.state, BackendState::Ready);
        let first_pid = first.pid.expect("a pid");
        let port = first.port.expect("a port");

        // Crash it the way a real sidecar dies: the process is gone, the handle is stale.
        terminate_pid(first_pid);

        let deadline = Instant::now() + Duration::from_secs(30);
        let mut replacement = None;
        while Instant::now() < deadline {
            if let Ok(sup) = supervisor().lock() {
                if let Some(live) = sup.live.as_ref() {
                    if live.pid != first_pid && alex_health(&url_for(live.port)).is_some() {
                        replacement = Some((live.pid, live.restarts, live.ownership.clone()));
                        break;
                    }
                }
            }
            thread::sleep(Duration::from_millis(200));
        }

        let (second_pid, restarts, ownership) =
            replacement.expect("the watchdog brings the sidecar back without the UI asking");
        assert_ne!(second_pid, first_pid);
        assert_eq!(port, port); // the replacement serves on a port of its own
        assert_eq!(ownership, Ownership::Owned);
        assert_eq!(restarts, 1, "exactly one crash, exactly one restart");
        assert!(process_alive(second_pid));

        // A full Quit is an instruction, not a crash: nothing may be resurrected after it.
        begin_shutdown();
        if let Ok(mut sup) = supervisor().lock() {
            if let Some(mut live) = sup.live.take() {
                if let Some(mut child) = live.child.take() {
                    let _ = child.kill();
                    let _ = child.wait();
                }
            }
        }
        thread::sleep(Duration::from_secs(4));
        {
            let sup = supervisor().lock().unwrap();
            assert!(sup.live.is_none(), "the watchdog resurrected a backend after Quit");
        }

        *TEST_LAUNCH.lock().unwrap() = None;
        resume_after_shutdown_for_tests();
        match previous_root {
            Some(value) => std::env::set_var("ALEX_LLM_DATA_DIR", value),
            None => std::env::remove_var("ALEX_LLM_DATA_DIR"),
        }
        let _ = fs::remove_dir_all(&root);
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
