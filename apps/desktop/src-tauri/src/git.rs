use crate::host::{critical_blocked, err_public, redact_text};
use serde_json::{json, Value};
use std::path::Path;
use std::time::Duration;

fn git_exe() -> Option<String> {
    [
        r"C:\Program Files\Git\cmd\git.exe",
        r"C:\Program Files (x86)\Git\cmd\git.exe",
    ]
    .into_iter()
    .map(str::to_string)
    .find(|path| Path::new(path).exists())
}

fn str_arg(args: &Value, key: &str) -> String {
    args.get(key)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim_matches('"')
        .to_string()
}

pub fn run_git(name: &str, args: &Value, roots: &[String], tool_run_id: &str) -> crate::host::LocalOutcome {
    if name == "git_push" && args.get("force").and_then(Value::as_bool).unwrap_or(false) {
        return critical_blocked("git_push", args);
    }
    if name == "git_reset" && str_arg(args, "mode") == "hard" {
        return critical_blocked("git_reset", args);
    }
    let Some(git) = git_exe() else {
        return err_public("git_not_installed");
    };
    let cwd = str_arg(args, "cwd");
    let Ok(dir) = crate::fs_guard::resolve(&cwd, roots) else {
        return err_public("path_denied");
    };
    let mut argv = vec![
        "-c".into(),
        "credential.helper=".into(),
        "-c".into(),
        "core.askPass=".into(),
    ];
    match name {
        "git_status" => argv.extend(["status".into(), "--porcelain=v1".into(), "-b".into()]),
        "git_diff" => argv.extend(["diff".into(), "--no-ext-diff".into()]),
        "git_log" => {
            let count = args.get("max_count").and_then(Value::as_u64).unwrap_or(10).min(50);
            argv.extend(["log".into(), "-n".into(), count.to_string(), "--format=%h %s".into()]);
        }
        "git_show" => {
            let rev = str_arg(args, "rev");
            if rev.is_empty() {
                return err_public("invalid_arguments");
            }
            argv.extend(["show".into(), "--no-ext-diff".into(), "--stat".into(), rev]);
        }
        "git_branch" => {
            if args.get("delete").and_then(Value::as_bool).unwrap_or(false) {
                let branch = str_arg(args, "name");
                if branch.is_empty() {
                    return err_public("invalid_arguments");
                }
                let flag = if args.get("force").and_then(Value::as_bool).unwrap_or(false) {
                    "-D"
                } else {
                    "-d"
                };
                argv.extend(["branch".into(), flag.into(), branch]);
            } else {
                argv.extend(["branch".into(), "--list".into()]);
            }
        }
        "git_add" => {
            argv.push("add".into());
            argv.push("--".into());
            let Some(paths) = args.get("paths").and_then(Value::as_array) else {
                return err_public("invalid_arguments");
            };
            for item in paths {
                let Some(path) = item.as_str() else {
                    continue;
                };
                if crate::fs_guard::resolve(path, roots).is_err() {
                    return err_public("path_denied");
                }
                argv.push(path.to_string());
            }
            if argv.len() <= 5 {
                return err_public("invalid_arguments");
            }
        }
        "git_commit" => {
            let message = str_arg(args, "message");
            if message.is_empty() {
                return err_public("invalid_arguments");
            }
            argv.extend(["commit".into(), "-m".into(), message, "--no-gpg-sign".into()]);
        }
        "git_restore" => {
            let path = str_arg(args, "path");
            if path.is_empty() || crate::fs_guard::resolve(&path, roots).is_err() {
                return err_public("path_denied");
            }
            argv.extend(["restore".into(), "--source=HEAD".into(), "--".into(), path]);
        }
        "git_push" => {
            if args.get("force").and_then(Value::as_bool).unwrap_or(false) {
                return critical_blocked("git_push", args);
            }
            let remote = str_arg(args, "remote");
            if remote.is_empty() {
                return err_public("invalid_arguments");
            }
            argv.extend(["push".into(), remote]);
            let branch = str_arg(args, "branch");
            if !branch.is_empty() {
                argv.push(branch);
            }
        }
        "git_reset" => {
            let mode = str_arg(args, "mode");
            if mode == "hard" {
                return critical_blocked("git_reset", args);
            }
            let flag = if mode == "soft" { "--soft" } else { "--mixed" };
            argv.extend(["reset".into(), flag.into(), str_arg(args, "ref")]);
        }
        _ => return err_public("unknown_tool"),
    }
    let mut outcome = crate::process::run_job(
        tool_run_id,
        &git,
        &argv,
        Some(&dir.to_string_lossy()),
        Duration::from_secs(60),
        || false,
        false,
    );
    outcome.stdout = redact_text(&outcome.stdout);
    outcome.stderr = redact_text(&outcome.stderr);
    outcome.text = redact_text(&outcome.text);
    if name == "git_status" {
        let dirty = outcome.stdout.lines().any(|line| {
            let trimmed = line.trim_start();
            !trimmed.is_empty() && !trimmed.starts_with("##")
        });
        let branch = outcome.stdout.lines().find_map(|line| {
            line.strip_prefix("## ")
                .map(|value| value.split("...").next().unwrap_or(value).to_string())
        });
        outcome.metadata = json!({ "dirty": dirty, "branch": branch });
    }
    outcome
}
