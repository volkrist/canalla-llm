#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::Manager;

mod auth;
mod autostart;
mod backend;
mod backup;
mod credential;
mod fs_guard;
mod gateway;
mod git;
mod host;
mod platform;
mod process;

fn main() {
    tauri::Builder::default()
        // A second launch brings the running product forward instead of starting a second one: one
        // desktop, one backend, one native host, one Tor. Registered first, because it has to
        // decide before any of them exists.
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        // Updates: check/download/verify against the bundled public key; install only when the user
        // asks for it, and only then restart through the process plugin.
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .setup(|_app| {
            // The Desktop owns the sidecar lifecycle: a child that dies is noticed and restarted
            // here, not by a window that may be closed, hidden or wedged.
            backend::start_watchdog();
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            host::pair_device,
            host::device_status,
            host::execute_host_jobs,
            host::forget_device,
            host::rotate_device_credential,
            host::store_user_credential,
            host::list_user_credentials,
            host::delete_user_credential,
            backend::ensure_backend,
            backend::backend_status,
            backend::restart_backend,
            auth::auth_login,
            auth::auth_register,
            auth::auth_bootstrap,
            auth::auth_restore,
            auth::auth_logout,
            auth::session_status,
            auth::provider_secret_configured,
            auth::set_provider_secret,
            auth::delete_provider_secret,
            gateway::gateway_status,
            gateway::gateway_enroll,
            gateway::gateway_disconnect,
            backup::restore_backup,
            backup::backup_location,
            autostart::autostart_status,
            autostart::set_autostart
        ])
        .build(tauri::generate_context!())
        .expect("Unable to start Canalla LLM")
        .run(|_app, event| {
            if matches!(
                event,
                tauri::RunEvent::Exit | tauri::RunEvent::ExitRequested { .. }
            ) {
                backend::on_desktop_exit();
            }
        });
}
