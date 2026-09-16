//! Canonical workspace path checks: no `..`, UNC, junction/symlink escape, or secret files.

use std::fs;
use std::path::{Path, PathBuf};

const SECRET_NAMES: &[&str] = &[
    ".env",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "login data",
    "logins.json",
    "key4.db",
    "signons.sqlite",
    "cookies.sqlite",
    "wallet.dat",
    "credentials.json",
    "ntuser.dat",
];

const SECRET_FRAGMENTS: &[&str] = &[
    "\\.ssh\\",
    "\\.gnupg\\",
    "\\appdata\\roaming\\microsoft\\credentials",
    "\\appdata\\local\\google\\chrome\\user data",
    "\\appdata\\roaming\\mozilla\\firefox",
    "\\appdata\\local\\microsoft\\edge\\user data",
    "\\1password\\",
    "\\keepass\\",
];

fn strip_extended(path: PathBuf) -> PathBuf {
    let text = path.to_string_lossy();
    if let Some(rest) = text.strip_prefix(r"\\?\") {
        PathBuf::from(rest)
    } else {
        path
    }
}

fn is_unc(value: &str) -> bool {
    let trimmed = value.trim();
    trimmed.starts_with(r"\\") || trimmed.starts_with("//")
}

fn deny_secret(canonical: &Path) -> Result<(), String> {
    let lowered = canonical.to_string_lossy().replace('/', "\\").to_lowercase();
    let name = canonical
        .file_name()
        .map(|v| v.to_string_lossy().to_lowercase())
        .unwrap_or_default();
    if SECRET_NAMES.iter().any(|item| name == *item)
        || name.starts_with(".env.")
        || name.ends_with(".pem")
        || name.ends_with(".ppk")
        || name.ends_with(".kdbx")
        || name.contains("wallet")
    {
        return Err("secret_path".into());
    }
    if SECRET_FRAGMENTS.iter().any(|item| lowered.contains(item)) {
        return Err("secret_path".into());
    }
    Ok(())
}

fn canonicalize_existing(path: &Path) -> Result<PathBuf, String> {
    fs::canonicalize(path)
        .map(strip_extended)
        .map_err(|_| "path_denied".to_string())
}

/// Resolve `path` against trusted roots. Non-existent files resolve via the parent directory.
pub fn resolve(path: &str, roots: &[String]) -> Result<PathBuf, String> {
    let value = path.trim();
    if value.is_empty() || is_unc(value) || value.contains('\0') {
        return Err("path_denied".into());
    }
    if Path::new(value).components().any(|part| matches!(part, std::path::Component::ParentDir)) {
        return Err("path_denied".into());
    }
    let candidate = PathBuf::from(value);
    let resolved = if candidate.exists() {
        canonicalize_existing(&candidate)?
    } else if let Some(parent) = candidate.parent().filter(|p| !p.as_os_str().is_empty()) {
        if parent.exists() {
            canonicalize_existing(parent)?.join(candidate.file_name().unwrap_or_default())
        } else {
            return Err("path_denied".into());
        }
    } else {
        return Err("path_denied".into());
    };
    deny_secret(&resolved)?;
    let mut allowed = false;
    for root in roots {
        let base = canonicalize_existing(Path::new(root)).or_else(|_| {
            PathBuf::from(root)
                .canonicalize()
                .map(strip_extended)
                .map_err(|_| "path_denied".to_string())
        });
        let Ok(base) = base else { continue };
        if resolved.starts_with(&base) {
            allowed = true;
            break;
        }
    }
    if !allowed {
        return Err("path_denied".into());
    }
    Ok(resolved)
}
