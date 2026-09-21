fn main() {
    let manifest = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
    let sidecar_dir = manifest.join("sidecar").join("alex-backend");
    let _ = std::fs::create_dir_all(&sidecar_dir);
    let keep = sidecar_dir.join(".keep");
    if !keep.is_file() {
        let _ = std::fs::write(&keep, b"");
    }
    let host = manifest.join("sidecar").join("alex-host-loop.exe");
    if !host.is_file() {
        let _ = std::fs::write(&host, b"");
    }
    let sidecar = sidecar_dir.join("alex-backend.exe");
    let release = std::env::var("PROFILE").unwrap_or_default() == "release";
    if release && !sidecar.is_file() {
        panic!(
            "BACKEND_SIDECAR_MISSING: run scripts/build-backend-sidecar.ps1 before a release build. Expected {}",
            sidecar.display()
        );
    }
    tauri_build::build()
}
