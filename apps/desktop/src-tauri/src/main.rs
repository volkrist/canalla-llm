#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod auth;
mod backend;
mod credential;
mod fs_guard;
mod git;
mod host;
mod process;

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
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
            auth::delete_provider_secret
        ])
        .build(tauri::generate_context!())
        .expect("Unable to start Alex LLM")
        .run(|_app, event| {
            if matches!(
                event,
                tauri::RunEvent::Exit | tauri::RunEvent::ExitRequested { .. }
            ) {
                backend::on_desktop_exit();
            }
        });
}
