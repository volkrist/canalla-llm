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
use std::io::Read;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex, OnceLock};
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

/// Keep at most this many bytes from each stream while still draining the pipe to EOF, so a child
/// that writes far more than the OS pipe buffer never blocks on a full pipe (A15).
const OUTPUT_BYTE_LIMIT: usize = 80_000;
const OUTPUT_CHAR_LIMIT: usize = 20_000;

/// Drain a child pipe on its own thread. It stops *keeping* bytes after `limit` but keeps reading,
/// so the writer is never stalled by a pipe nobody is emptying.
fn drain_stream(
    mut reader: impl Read + Send + 'static,
    sink: Arc<Mutex<Vec<u8>>>,
    limit: usize,
) -> std::thread::JoinHandle<()> {
    std::thread::spawn(move || {
        let mut buf = [0u8; 8192];
        loop {
            match reader.read(&mut buf) {
                Ok(0) => break,
                Ok(n) => {
                    if let Ok(mut data) = sink.lock() {
                        if data.len() < limit {
                            let room = limit - data.len();
                            data.extend_from_slice(&buf[..n.min(room)]);
                        }
                    }
                }
                Err(_) => break,
            }
        }
    })
}

fn collected(sink: &Arc<Mutex<Vec<u8>>>) -> String {
    let data = sink.lock().map(|guard| guard.clone()).unwrap_or_default();
    String::from_utf8_lossy(&data)
        .chars()
        .take(OUTPUT_CHAR_LIMIT)
        .collect()
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
    // Read both pipes in parallel from the very first byte: the old runner read them only after the
    // child exited, so enough output filled the OS pipe and the child blocked forever.
    let stdout_sink = Arc::new(Mutex::new(Vec::new()));
    let stderr_sink = Arc::new(Mutex::new(Vec::new()));
    let mut stdout_handle = child
        .stdout
        .take()
        .map(|stream| drain_stream(stream, stdout_sink.clone(), OUTPUT_BYTE_LIMIT));
    let mut stderr_handle = child
        .stderr
        .take()
        .map(|stream| drain_stream(stream, stderr_sink.clone(), OUTPUT_BYTE_LIMIT));
    let started = Instant::now();
    let mut exit_status = None;
    loop {
        if should_stop() || started.elapsed() > timeout {
            stop_job(tool_run_id);
            let _ = child.kill();
            let _ = child.wait();
            if let Some(handle) = stdout_handle.take() {
                let _ = handle.join();
            }
            if let Some(handle) = stderr_handle.take() {
                let _ = handle.join();
            }
            return LocalOutcome {
                exit_code: None,
                stdout: collected(&stdout_sink),
                stderr: "stopped".into(),
                text: "stopped".into(),
                metadata: json!({"status": "stopped", "pid": pid}),
            };
        }
        match child.try_wait() {
            Ok(Some(status)) => {
                exit_status = Some(status);
                break;
            }
            Ok(None) => std::thread::sleep(Duration::from_millis(80)),
            Err(_) => break,
        }
    }
    jobs().lock().expect("job map").remove(tool_run_id);
    if let Some(handle) = stdout_handle.take() {
        let _ = handle.join();
    }
    if let Some(handle) = stderr_handle.take() {
        let _ = handle.join();
    }
    let stdout = collected(&stdout_sink);
    let stderr = collected(&stderr_sink);
    let combined = if stderr.is_empty() {
        stdout.clone()
    } else if stdout.is_empty() {
        stderr.clone()
    } else {
        format!("{stdout}\nstderr:\n{stderr}")
    };
    LocalOutcome {
        exit_code: exit_status.and_then(|status| status.code()),
        text: combined.chars().take(OUTPUT_CHAR_LIMIT).collect(),
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
    let mut command = format!(
        "Start-Process -Verb RunAs -Wait -FilePath {}",
        ps_quote(exe)
    );
    if !args.is_empty() {
        let list = args
            .iter()
            .map(|item| ps_quote(item))
            .collect::<Vec<_>>()
            .join(",");
        command.push_str(&format!(" -ArgumentList {list}"));
    }
    if let Some(dir) = cwd {
        command.push_str(&format!(" -WorkingDirectory {}", ps_quote(dir)));
    }
    let powershell = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe";
    run_job(
        "elevate",
        powershell,
        &[
            "-NoProfile".into(),
            "-NonInteractive".into(),
            "-Command".into(),
            command,
        ],
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

    /// A15: draining must keep reading past the cap, or a child that writes more than the OS pipe
    /// buffer would block forever. The sink keeps only the first `limit` bytes but reads to EOF.
    #[test]
    fn drain_stream_reads_past_the_cap_without_blocking() {
        let payload = vec![b'x'; 5_000_000];
        let sink = std::sync::Arc::new(std::sync::Mutex::new(Vec::new()));
        let handle = super::drain_stream(
            std::io::Cursor::new(payload),
            sink.clone(),
            super::OUTPUT_BYTE_LIMIT,
        );
        handle.join().unwrap();
        let data = sink.lock().unwrap();
        assert_eq!(data.len(), super::OUTPUT_BYTE_LIMIT);
    }

    #[test]
    fn collected_caps_the_text_length() {
        let sink = std::sync::Arc::new(std::sync::Mutex::new(vec![b'x'; super::OUTPUT_BYTE_LIMIT]));
        let text = super::collected(&sink);
        assert_eq!(text.chars().count(), super::OUTPUT_CHAR_LIMIT);
    }
}
