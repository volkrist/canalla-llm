#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod auth;
mod backend;
mod backup;
mod credential;
mod fs_guard;
mod gateway;
mod git;
mod host;
mod process;

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
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
            backup::backup_location
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
