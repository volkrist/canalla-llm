import { invoke } from "@tauri-apps/api/core";

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
  return invoke<BackendRuntime>("ensure_backend");
}

/** Ask the Desktop to stop what it owns and start a fresh local backend.
 *
 * `ensure_backend` is the normal path (it restarts a sidecar it saw die). This one is the explicit
 * recovery: it does not depend on the supervisor's own bookkeeping, so a crash that left it in a
 * stale state still ends with a working backend. */
export async function restartBackend(): Promise<BackendRuntime | null> {
  if (!isTauriRuntime()) return null;
  return invoke<BackendRuntime>("restart_backend");
}

/** How a crashed owned backend is brought back: a few bounded attempts, never a hot loop.
 *
 * The Desktop restarts an owned sidecar it saw die (`ensure_backend`), and it budgets one restart
 * per session, so the watchdog must be equally bounded: three attempts, one of them immediate,
 * then the honest failure stays on screen instead of a loop nobody can stop. */
export const RECOVERY_DELAYS_MS: readonly number[] = [0, 2000, 6000];

export function recoveryDelay(attempt: number): number | null {
  if (
    !Number.isInteger(attempt) ||
    attempt < 0 ||
    attempt >= RECOVERY_DELAYS_MS.length
  )
    return null;
  return RECOVERY_DELAYS_MS[attempt];
}

export interface RecoveryAttempt {
  attempted: boolean;
  state: BackendState | null;
}

/** A recovery step must never hang the UI: one stuck command cannot freeze the watchdog.
 *
 * The Rust side keeps working on the request after this gives up, so a slow spawn is not lost -
 * the app simply stops *waiting* on it and re-reads the state instead. */
export const RECOVERY_CALL_TIMEOUT_MS = 20000;

export async function withTimeout<T>(
  work: Promise<T>,
  ms: number,
): Promise<T | null> {
  return Promise.race([
    work.catch(() => null),
    new Promise<null>((done) => setTimeout(() => done(null), ms)),
  ]);
}

/** One bounded attempt to get the local backend back, with the seams a test needs.
 *
 * The first attempt asks the supervisor to re-ensure what it already owns; the later ones ask for
 * an explicit restart, because a crashed sidecar can leave the supervisor reporting "starting"
 * without anything actually coming up. */
export async function recoverBackend(
  attempt: number,
  seams: {
    ensure?: () => Promise<BackendRuntime | null>;
    restart?: () => Promise<BackendRuntime | null>;
    sleep?: (ms: number) => Promise<void>;
    timeoutMs?: number;
  } = {},
): Promise<RecoveryAttempt> {
  const delay = recoveryDelay(attempt);
  if (delay === null) return { attempted: false, state: null };
  const wait =
    seams.sleep ??
    ((ms: number) => new Promise((done) => setTimeout(done, ms)));
  if (delay > 0) await wait(delay);
  const call =
    attempt === 0
      ? (seams.ensure ?? ensureBackend)
      : (seams.restart ?? restartBackend);
  const next = await withTimeout(
    call(),
    seams.timeoutMs ?? RECOVERY_CALL_TIMEOUT_MS,
  );
  return { attempted: true, state: next?.state ?? null };
}
