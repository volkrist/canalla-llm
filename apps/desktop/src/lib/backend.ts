export type BackendState = "starting" | "ready" | "error";
export type BackendOwnership = "none" | "owned" | "external";

export interface BackendRuntime {
  state: BackendState;
  ownership: BackendOwnership;
  url?: string | null;
  port?: number | null;
  pid?: number | null;
  error?: string | null;
  data_dir: string;
}

export function isTauriRuntime(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

export function backendMessage(status: BackendRuntime | null): string {
  if (!status) return "Проверяю локальный сервер…";
  if (status.state === "starting") return "Запускаю локальный сервер…";
  if (status.state === "ready" && status.ownership === "external") {
    return "Подключено к уже запущенному backend.";
  }
  if (status.state === "ready") return "";
  const code = status.error || "backend_error";
  if (code === "migration_failed") {
    return "Не удалось обновить базу. Чат не запущен; данные не удалены.";
  }
  if (code === "backend_python_missing") {
    return "Не найден Python backend. Для разработки выполните setup-backend.ps1.";
  }
  if (code === "no_safe_backend_port") {
    return "Порт занят другим приложением. Alex не завершает чужие процессы.";
  }
  return `Локальный сервер не запустился (${code}).`;
}

export async function ensureBackend(): Promise<BackendRuntime | null> {
  if (!isTauriRuntime()) return null;
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<BackendRuntime>("ensure_backend");
}
