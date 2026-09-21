export type BackendState = "starting" | "ready" | "error";
export type BackendOwnership = "none" | "owned" | "external";
export type BackendRuntimeMode =
  "none" | "packaged" | "dev_owned" | "dev_external";

export interface BackendRuntime {
  state: BackendState;
  ownership: BackendOwnership;
  url?: string | null;
  port?: number | null;
  pid?: number | null;
  error?: string | null;
  data_dir: string;
  runtime_mode?: BackendRuntimeMode;
  diagnostic?: string | null;
}

export function isTauriRuntime(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

const ERRORS: Record<string, string> = {
  BACKEND_SIDECAR_MISSING:
    "Не найден встроенный сервер. Переустановите Canalla LLM.",
  BACKEND_START_FAILED: "Локальный сервер не запустился.",
  BACKEND_HEALTH_TIMEOUT: "Локальный сервер не ответил вовремя.",
  BACKEND_VERSION_MISMATCH:
    "Версия локального сервера не совпадает с приложением.",
  MIGRATION_FAILED:
    "Не удалось обновить базу. Чат не запущен; данные не удалены.",
  NO_SAFE_BACKEND_PORT:
    "Порт занят другим приложением. Canalla LLM не завершает чужие процессы.",
  BACKEND_STOP_TIMEOUT:
    "Локальный сервер не остановился вовремя. Закройте Canalla LLM и повторите.",
  DATA_ROOT_UNAVAILABLE: "Нет доступа к папке данных пользователя.",
  JWT_SECRET_FAILED: "Не удалось подготовить локальный ключ входа.",
  migration_failed:
    "Не удалось обновить базу. Чат не запущен; данные не удалены.",
  backend_python_missing:
    "Не найден Python backend. Для разработки выполните setup-backend.ps1.",
  no_safe_backend_port:
    "Порт занят другим приложением. Canalla LLM не завершает чужие процессы.",
};

export function backendMessage(status: BackendRuntime | null): string {
  if (!status) return "Проверяю локальный сервер…";
  if (status.state === "starting") return "Запускаю локальный сервер…";
  if (status.state === "ready" && status.ownership === "external") {
    if (status.diagnostic === "BACKEND_VERSION_MISMATCH") {
      return "Подключено к уже запущенному backend. Версия отличается от приложения.";
    }
    return "Подключено к уже запущенному backend.";
  }
  if (status.state === "ready") return "";
  const code = status.error || "backend_error";
  return ERRORS[code] || `Локальный сервер не запустился (${code}).`;
}

export async function ensureBackend(): Promise<BackendRuntime | null> {
  if (!isTauriRuntime()) return null;
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<BackendRuntime>("ensure_backend");
}
