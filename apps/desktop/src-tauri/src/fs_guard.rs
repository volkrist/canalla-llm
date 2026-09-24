//! Canonical local path checks: no `..`, UNC, or secret files. Trusted roots are not a jail.

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
    if trimmed.starts_with(r"\\?\") {
        return true;
    }
    trimmed.starts_with(r"\\") || trimmed.starts_with("//")
}

/// Something this tool may touch: a drive-letter path on Windows, an absolute path on POSIX. A
/// relative path is denied on both, because what it resolves to depends on a working directory the
/// user never chose.
fn is_local_absolute(value: &str) -> bool {
    #[cfg(windows)]
    {
        let bytes = value.as_bytes();
        bytes.len() >= 2 && bytes[0].is_ascii_alphabetic() && bytes[1] == b':'
    }
    #[cfg(not(windows))]
    {
        value.starts_with('/')
    }
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

/// Resolve a local absolute path. Trusted workspace roots do not restrict access.
pub fn resolve(path: &str, _roots: &[String]) -> Result<PathBuf, String> {
    let value = path.trim();
    if value.is_empty() || is_unc(value) || value.contains('\0') || !is_local_absolute(value) {
        return Err("path_denied".into());
    }
    if Path::new(value)
        .components()
        .any(|part| matches!(part, std::path::Component::ParentDir))
    {
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
    Ok(resolved)
}

#[allow(dead_code)]
pub fn inside_trusted(path: &Path, roots: &[String]) -> bool {
    if roots.is_empty() {
        return false;
    }
    for root in roots {
        let base = canonicalize_existing(Path::new(root)).or_else(|_| {
            PathBuf::from(root)
                .canonicalize()
                .map(strip_extended)
                .map_err(|_| "path_denied".to_string())
        });
        let Ok(base) = base else { continue };
        if path.starts_with(&base) {
            return true;
        }
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::env;

    #[test]
    fn a_local_absolute_path_is_allowed() {
        #[cfg(windows)]
        {
            let roots = vec![r"C:\AlexWorkspace".to_string()];
            assert!(resolve(r"C:\Windows", &roots).is_ok());
            assert!(resolve(r"\\server\share\file", &[]).is_err());
            assert!(resolve(r"C:\AlexWorkspace\..\Windows\win.ini", &roots).is_err());
            assert!(resolve("notes.txt", &[]).is_err());
        }
        #[cfg(not(windows))]
        {
            let tmp = env::temp_dir();
            assert!(resolve(tmp.to_str().unwrap(), &[]).is_ok());
            assert!(resolve("//server/share/file", &[]).is_err());
            assert!(resolve("/tmp/../etc/passwd", &[]).is_err());
            assert!(resolve("notes.txt", &[]).is_err());
        }
    }

    #[test]
    fn secret_names_denied() {
        let dir = env::temp_dir().join("alex-llm-fs-secret");
        let _ = fs::create_dir_all(&dir);
        let secret = dir.join(".env");
        fs::write(&secret, "x").unwrap();
        assert!(resolve(secret.to_str().unwrap(), &[]).is_err());
        let _ = fs::remove_file(&secret);
        let _ = fs::remove_dir(&dir);
    }

    #[test]
    fn trusted_helper_does_not_jail_resolve() {
        let tmp = env::temp_dir().join("alex-llm-trusted-root");
        fs::create_dir_all(&tmp).unwrap();
        let outside = env::temp_dir();
        let roots = vec![tmp.to_string_lossy().into_owned()];
        let resolved = resolve(outside.to_str().unwrap(), &roots).unwrap();
        assert!(!inside_trusted(&resolved, &roots));
        let inner = tmp.join("file.txt");
        fs::write(&inner, "ok").unwrap();
        let resolved_inner = resolve(inner.to_str().unwrap(), &roots).unwrap();
        assert!(inside_trusted(&resolved_inner, &roots));
        let _ = fs::remove_file(&inner);
        let _ = fs::remove_dir(&tmp);
    }
}
