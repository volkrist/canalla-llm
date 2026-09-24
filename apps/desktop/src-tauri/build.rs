//! The resources a release build must carry, checked before Tauri's own build script runs.
//!
//! The names are the platform's: Windows ships `alex-backend.exe` and `alex-host-loop.exe`, Linux
//! ships `alex-backend` and `alex-host-loop`. A placeholder is written for resources that are only
//! needed by a packaged build, because a development build must not fail on their absence.
//!
//! A *release* build refuses to continue without the packaged backend: shipping a desktop whose
//! sidecar is missing would produce an installation that starts and then cannot serve anything.

fn exe(name: &str) -> String {
    if cfg!(windows) {
        format!("{name}.exe")
    } else {
        name.to_string()
    }
}

fn main() {
    let manifest = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
    let sidecar_dir = manifest.join("sidecar").join("alex-backend");
    let _ = std::fs::create_dir_all(&sidecar_dir);
    let keep = sidecar_dir.join(".keep");
    if !keep.is_file() {
        let _ = std::fs::write(&keep, b"");
    }
    let host = manifest.join("sidecar").join(exe("alex-host-loop"));
    if !host.is_file() {
        let _ = std::fs::write(&host, b"");
    }
    let sidecar = sidecar_dir.join(exe("alex-backend"));
    let release = std::env::var("PROFILE").unwrap_or_default() == "release";
    if release && !sidecar.is_file() {
        panic!(
            "BACKEND_SIDECAR_MISSING: build the packaged backend before a release build \
             (scripts/build-backend-sidecar.ps1 on Windows, apps/backend/alex-backend.spec with \
             PyInstaller elsewhere). Expected {}",
            sidecar.display()
        );
    }
    tauri_build::build()
}
