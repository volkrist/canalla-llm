//! Owned tool processes: one child, owned so that stopping it stops what it started.
//!
//! The desktop runs the tools the user asked for (a build, a script, a git command) and must be able
//! to stop them again — including anything they started. How that ownership works is the platform's
//! business (`crate::platform`): a kill-on-close Job Object on Windows, a process group plus a
//! parent-death signal on Linux. Nothing in this file knows which one is in use.

use crate::host::LocalOutcome;
use crate::platform::OwnedJob;
use serde_json::json;
use std::collections::HashMap;
use std::process::{Child, Command, Stdio};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

struct JobEntry {
    /// Held for as long as the run is: dropping it is what stops the tree on Windows.
    #[allow(dead_code)]
    job: Option<OwnedJob>,
    child: Option<Child>,
    pid: u32,
}

static JOBS: OnceLock<Mutex<HashMap<String, JobEntry>>> = OnceLock::new();

fn jobs() -> &'static Mutex<HashMap<String, JobEntry>> {
    JOBS.get_or_init(|| Mutex::new(HashMap::new()))
}

/// The environment a tool may see: the machine's own variables, never this application's secrets.
pub fn sanitized_env() -> HashMap<String, String> {
    crate::platform::sanitized_env()
}

pub fn run_job(
    tool_run_id: &str,
    exe: &str,
    args: &[String],
    cwd: Option<&str>,
    timeout: Duration,
    should_stop: impl Fn() -> bool,
    elevate: bool,
    wait: bool,
) -> LocalOutcome {
    // A platform without an elevation helper the product may drive says so, instead of running the
    // tool unelevated and reporting success.
    if elevate && !crate::platform::elevation_available() {
        return err("elevation_unavailable");
    }
    if elevate {
        return run_elevated(exe, args, cwd);
    }
    let mut command = Command::new(exe);
    command
        .args(args)
        .env_clear()
        .envs(sanitized_env())
        .stdin(Stdio::null())
        .stdout(if wait { Stdio::piped() } else { Stdio::null() })
        .stderr(if wait { Stdio::piped() } else { Stdio::null() });
    if let Some(dir) = cwd {
        command.current_dir(dir);
    }
    crate::platform::configure_spawn(&mut command);
    let mut child = match command.spawn() {
        Ok(child) => child,
        Err(_) => {
            crate::platform::configure_spawn_plain(&mut command);
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
    let job = crate::platform::own_child(&child);
    let pid = child.id();
    if !wait {
        jobs().lock().expect("job map").insert(
            tool_run_id.to_string(),
            JobEntry {
                job,
                child: Some(child),
                pid,
            },
        );
        return LocalOutcome {
            exit_code: None,
            stdout: String::new(),
            stderr: String::new(),
            text: format!("started pid={pid}"),
            metadata: json!({"pid": pid, "status": "running", "started_by_alex": true}),
        };
    }
    jobs().lock().expect("job map").insert(
        tool_run_id.to_string(),
        JobEntry {
            job,
            child: None,
            pid,
        },
    );
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
                metadata: json!({"status": "stopped", "pid": pid}),
            };
        }
        match child.try_wait() {
            Ok(Some(_)) => break,
            Ok(None) => std::thread::sleep(Duration::from_millis(80)),
            Err(_) => break,
        }
    }
    jobs().lock().expect("job map").remove(tool_run_id);
    let output = child.wait_with_output().ok();
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
        metadata: json!({"pid": pid}),
    }
}

pub fn stop_job(tool_run_id: &str) -> Option<u32> {
    let mut entry = jobs().lock().ok()?.remove(tool_run_id)?;
    let pid = entry.pid;
    let had_job = entry.job.is_some();
    if let Some(mut child) = entry.child.take() {
        let _ = child.kill();
        let _ = child.wait();
    }
    // Dropping the ownership handle stops the tree where the platform has one; where it does not
    // (POSIX process groups), the group is stopped explicitly.
    drop(entry.job);
    if !had_job && pid != 0 {
        crate::platform::terminate_tree(pid);
    }
    Some(pid)
}

pub fn job_status(tool_run_id: &str) -> &'static str {
    let Ok(mut map) = jobs().lock() else {
        return "unknown";
    };
    let Some(entry) = map.get_mut(tool_run_id) else {
        return "unknown";
    };
    if let Some(child) = entry.child.as_mut() {
        return match child.try_wait() {
            Ok(None) => "running",
            Ok(Some(_)) => "exited",
            Err(_) => "unknown",
        };
    }
    "running"
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

/// A Windows UAC prompt; Alex never stores an admin password or bypasses UAC. A platform without an
/// equivalent the product may drive says so instead of pretending.
#[cfg(windows)]
fn run_elevated(exe: &str, args: &[String], cwd: Option<&str>) -> LocalOutcome {
    fn ps_quote(value: &str) -> String {
        format!("'{}'", value.replace('\'', "''"))
    }
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
        true,
    )
}

#[cfg(not(windows))]
fn run_elevated(_exe: &str, _args: &[String], _cwd: Option<&str>) -> LocalOutcome {
    // Linux has no elevation helper the product drives: a privileged action is the user's own
    // business through their own tools, and Canalla never asks for a password.
    err("elevation_unavailable")
}

#[cfg(test)]
mod tests {
    #[test]
    fn sanitized_env_omits_application_secrets() {
        let _guard = crate::credential::TEST_ENV_LOCK.lock().unwrap();
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
