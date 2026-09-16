//! Windows Job Object runner: children inherit the job; Stop closes only this job.

use crate::host::LocalOutcome;
use serde_json::json;
use std::collections::HashMap;
use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
use std::os::windows::process::CommandExt;
use std::process::{Command, Stdio};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

const CREATE_NO_WINDOW: u32 = 0x0800_0000;
const CREATE_UNICODE_ENVIRONMENT: u32 = 0x0000_0400;
const CREATE_BREAKAWAY_FROM_JOB: u32 = 0x0100_0000;

static JOBS: OnceLock<Mutex<HashMap<String, OwnedHandle>>> = OnceLock::new();

fn jobs() -> &'static Mutex<HashMap<String, OwnedHandle>> {
    JOBS.get_or_init(|| Mutex::new(HashMap::new()))
}

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
        "OS",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
    ] {
        if let Ok(value) = std::env::var(key) {
            env.insert(key.to_string(), value);
        }
    }
    env.insert(
        "PATH".into(),
        format!(
            r"{root}\System32;{root}\System32\WindowsPowerShell\v1.0;{root}\System32\Wbem",
            root = system_root
        ),
    );
    env
}

fn create_kill_on_close_job() -> Result<OwnedHandle, String> {
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
        Ok(OwnedHandle::from_raw_handle(job.0))
    }
}

fn assign(job: &OwnedHandle, process: std::os::windows::io::RawHandle) -> Result<(), String> {
    use windows::Win32::Foundation::HANDLE;
    use windows::Win32::System::JobObjects::AssignProcessToJobObject;
    unsafe {
        AssignProcessToJobObject(HANDLE(job.as_raw_handle()), HANDLE(process)).map_err(|e| e.to_string())
    }
}

pub fn run_job(
    tool_run_id: &str,
    exe: &str,
    args: &[String],
    cwd: Option<&str>,
    timeout: Duration,
    should_stop: impl Fn() -> bool,
) -> LocalOutcome {
    let job = match create_kill_on_close_job() {
        Ok(job) => job,
        Err(error) => return err(&error),
    };
    let mut command = Command::new(exe);
    command
        .args(args)
        .env_clear()
        .envs(sanitized_env())
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .creation_flags(CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT | CREATE_BREAKAWAY_FROM_JOB);
    if let Some(dir) = cwd {
        command.current_dir(dir);
    }
    let mut child = match command.spawn() {
        Ok(child) => child,
        Err(_) => return err("invalid_arguments"),
    };
    if assign(&job, child.as_raw_handle()).is_err() {
        let _ = child.kill();
        return err("job_assign_failed");
    }
    jobs()
        .lock()
        .expect("job map")
        .insert(tool_run_id.to_string(), job);
    let started = Instant::now();
    loop {
        if should_stop() || started.elapsed() > timeout {
            stop_job(tool_run_id);
            let _ = child.kill();
            let output = child.wait_with_output().ok();
            return LocalOutcome {
                exit_code: output.as_ref().and_then(|o| o.status.code()),
                stdout: output
                    .as_ref()
                    .map(|o| String::from_utf8_lossy(&o.stdout).into_owned())
                    .unwrap_or_default(),
                stderr: "stopped".into(),
                text: "stopped".into(),
                metadata: json!({"status": "stopped"}),
            };
        }
        match child.try_wait() {
            Ok(Some(_)) => break,
            Ok(None) => std::thread::sleep(Duration::from_millis(80)),
            Err(_) => break,
        }
    }
    let output = child.wait_with_output().ok();
    stop_job(tool_run_id);
    let stdout: String = output
        .as_ref()
        .map(|o| String::from_utf8_lossy(&o.stdout).chars().take(20000).collect())
        .unwrap_or_default();
    let stderr: String = output
        .as_ref()
        .map(|o| String::from_utf8_lossy(&o.stderr).chars().take(20000).collect())
        .unwrap_or_default();
    LocalOutcome {
        exit_code: output.as_ref().and_then(|o| o.status.code()),
        text: stdout.clone(),
        stdout,
        stderr,
        metadata: json!({}),
    }
}

pub fn stop_job(tool_run_id: &str) {
    if let Ok(mut map) = jobs().lock() {
        map.remove(tool_run_id);
    }
}

fn err(code: &str) -> LocalOutcome {
    LocalOutcome {
        exit_code: Some(1),
        stdout: String::new(),
        stderr: code.into(),
        text: code.into(),
        metadata: json!({"error": code}),
    }
}
