//! Native Windows host loop for production-like E2E without a WebView.
//! Uses the same host/credential/process/git modules as the Tauri app.
#![allow(dead_code)]

#[path = "../credential.rs"]
mod credential;
#[path = "../fs_guard.rs"]
mod fs_guard;
#[path = "../git.rs"]
mod git;
#[path = "../host.rs"]
mod host;
#[path = "../process.rs"]
mod process;

use serde_json::json;
use std::io::{self, Write};
use std::path::PathBuf;
use std::time::Duration;

#[tokio::main]
async fn main() {
    let backend = std::env::var("ALEX_BACKEND_URL").unwrap_or_else(|_| "http://127.0.0.1:8000".into());
    let token = match std::env::var("ALEX_TOKEN") {
        Ok(value) if !value.is_empty() => value,
        _ => {
            eprintln!("ALEX_TOKEN is required");
            std::process::exit(2);
        }
    };
    let name = std::env::var("ALEX_DEVICE_NAME").unwrap_or_else(|_| "E2E native host".into());
    let roots: Vec<String> = std::env::var("ALEX_WORKSPACE_ROOTS")
        .unwrap_or_default()
        .split(';')
        .map(str::trim)
        .filter(|item| !item.is_empty())
        .map(|item| item.to_string())
        .collect();
    match host::pair_device(backend.clone(), token.clone(), name).await {
        Ok(status) => {
            let line = json!({
                "event": "paired",
                "device_id": status.get("device_id"),
                "storage": status.get("storage"),
                "online": status.get("online"),
            });
            println!("{line}");
            let _ = io::stdout().flush();
        }
        Err(error) => {
            eprintln!("pair_failed={error}");
            std::process::exit(1);
        }
    }
    loop {
        if hold_jobs() {
            let _ = heartbeat(&backend, &token).await;
            tokio::time::sleep(Duration::from_millis(250)).await;
            continue;
        }
        maybe_post_wrong_digest(&backend, &token).await;
        match host::execute_host_jobs(backend.clone(), token.clone(), roots.clone()).await {
            Ok(value) => {
                let started = value.get("started").and_then(|item| item.as_u64()).unwrap_or(0);
                if started > 0 {
                    println!("{}", json!({ "event": "jobs", "started": started }));
                    let _ = io::stdout().flush();
                }
            }
            Err(error) => {
                eprintln!("host_loop_error={error}");
                let _ = io::stderr().flush();
            }
        }
        tokio::time::sleep(Duration::from_millis(400)).await;
    }
}

fn hold_jobs() -> bool {
    std::env::var("ALEX_E2E_HOLD_FLAG")
        .ok()
        .map(PathBuf::from)
        .is_some_and(|path| path.exists())
}

fn wrong_digest_flag() -> Option<PathBuf> {
    std::env::var("ALEX_E2E_WRONG_DIGEST_FLAG")
        .ok()
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
}

async fn heartbeat(backend: &str, token: &str) -> Result<(), String> {
    let headers = host::device_headers(token)?;
    let _ = reqwest::Client::new()
        .post(format!("{backend}/tools/devices/heartbeat"))
        .headers(headers)
        .send()
        .await;
    Ok(())
}

async fn maybe_post_wrong_digest(backend: &str, token: &str) {
    let Some(flag) = wrong_digest_flag() else {
        return;
    };
    if !flag.exists() {
        return;
    }
    let Ok(headers) = host::device_headers(token) else {
        return;
    };
    let client = reqwest::Client::new();
    let Ok(response) = client
        .get(format!("{backend}/tools/devices/jobs"))
        .headers(headers.clone())
        .send()
        .await
    else {
        return;
    };
    let Ok(jobs) = response.json::<Vec<serde_json::Value>>().await else {
        return;
    };
    let Some(job) = jobs.first() else {
        return;
    };
    let Some(id) = job.get("id").and_then(|value| value.as_str()) else {
        return;
    };
    let posted = client
        .post(format!("{backend}/tools/runs/{id}/host-result"))
        .headers(headers)
        .json(&json!({
            "digest": "0".repeat(64),
            "status": "completed",
            "text": "mismatch",
            "stdout": "",
            "stderr": "",
            "metadata": {}
        }))
        .send()
        .await;
    let status = posted.map(|response| response.status().as_u16()).unwrap_or(0);
    let report = flag.with_extension("json");
    let _ = std::fs::write(
        report,
        json!({ "http": status, "device_auth": true, "method": "native_host_wrong_digest" }).to_string(),
    );
    let _ = std::fs::remove_file(&flag);
    println!("{}", json!({ "event": "wrong_digest", "http": status }));
    let _ = io::stdout().flush();
}
