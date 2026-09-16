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
