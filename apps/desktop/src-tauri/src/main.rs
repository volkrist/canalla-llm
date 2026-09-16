#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod credential;
mod fs_guard;
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
            host::execute_host_jobs
        ])
        .run(tauri::generate_context!())
        .expect("Unable to start Alex LLM");
}
