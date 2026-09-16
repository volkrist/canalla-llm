#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

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
            host::delete_user_credential
        ])
        .run(tauri::generate_context!())
        .expect("Unable to start Alex LLM");
}
