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
        "LOCALAPPDATA",
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
        {
            let mut path = format!(
                r"{root}\System32;{root}\System32\WindowsPowerShell\v1.0;{root}\System32\Wbem",
                root = system_root
            );
            for git_dir in [r"C:\Program Files\Git\cmd", r"C:\Program Files (x86)\Git\cmd"] {
                if std::path::Path::new(git_dir).exists() {
                    path.push(';');
                    path.push_str(git_dir);
                }
            }
            if let Ok(local) = std::env::var("LOCALAPPDATA") {
                let apps = std::path::PathBuf::from(local)
                    .join("Microsoft")
                    .join("WindowsApps");
                if apps.is_dir() {
                    path.push(';');
                    path.push_str(&apps.to_string_lossy());
                }
            }
            path
        },
    );
    env.insert("GIT_TERMINAL_PROMPT".into(), "0".into());
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
    elevate: bool,
) -> LocalOutcome {
    if elevate {
        return run_elevated(exe, args, cwd);
    }
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
        Err(_) => {
            command.creation_flags(CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT);
            match command.spawn() {
                Ok(child) => child,
                Err(error) => {
                    return LocalOutcome {
                        exit_code: Some(1),
                        stdout: String::new(),
                        stderr: error.to_string(),
                        text: "invalid_arguments".into(),
                        metadata: json!({"error": "invalid_arguments", "detail": error.to_string()}),
                    };
                }
            }
        }
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
    let combined = if stderr.is_empty() {
        stdout.clone()
    } else if stdout.is_empty() {
        stderr.clone()
    } else {
        format!("{stdout}\nstderr:\n{stderr}")
    };
    LocalOutcome {
        exit_code: output.as_ref().and_then(|o| o.status.code()),
        text: combined.chars().take(20000).collect(),
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

pub fn job_status(tool_run_id: &str) -> &'static str {
    match jobs().lock() {
        Ok(map) if map.contains_key(tool_run_id) => "running",
        _ => "unknown",
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

fn ps_quote(value: &str) -> String {
    format!("'{}'", value.replace('\'', "''"))
}

fn run_elevated(exe: &str, args: &[String], cwd: Option<&str>) -> LocalOutcome {
    // Windows UAC prompt. Alex never stores an admin password or bypasses UAC.
    let mut command = format!("Start-Process -Verb RunAs -Wait -FilePath {}", ps_quote(exe));
    if !args.is_empty() {
        let list = args.iter().map(|item| ps_quote(item)).collect::<Vec<_>>().join(",");
        command.push_str(&format!(" -ArgumentList {list}"));
    }
    if let Some(dir) = cwd {
        command.push_str(&format!(" -WorkingDirectory {}", ps_quote(dir)));
    }
    let powershell = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe";
    run_job(
        "elevate",
        powershell,
        &["-NoProfile".into(), "-NonInteractive".into(), "-Command".into(), command],
        None,
        Duration::from_secs(120),
        || false,
        false,
    )
}

#[cfg(test)]
mod tests {
    #[test]
    fn sanitized_env_omits_application_secrets() {
        std::env::set_var("RUNPOD_API_KEY", "secret");
        std::env::set_var("TINYFISH_API_KEY", "secret");
        std::env::set_var("LLM_API_KEY", "secret");
        std::env::set_var("JWT_SECRET", "secret");
        std::env::set_var("ALEX_DEVICE_CREDENTIAL", "secret");
        let env = super::sanitized_env();
        for key in [
            "RUNPOD_API_KEY",
            "TINYFISH_API_KEY",
            "LLM_API_KEY",
            "JWT_SECRET",
            "ALEX_DEVICE_CREDENTIAL",
        ] {
            assert!(!env.contains_key(key));
        }
        assert!(env.contains_key("SystemRoot") || env.contains_key("PATH"));
    }
}
