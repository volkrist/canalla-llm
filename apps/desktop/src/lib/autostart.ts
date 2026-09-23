// «Запускать Canalla вместе с Windows»: the setting is the user's policy, the registry is the truth.
//
// The Rust side owns the Windows `Run` value and reads it back after every change, so this module
// never assumes a write worked: the toggle renders what the operating system reports, and a
// mismatch between the stored setting and the machine is reconciled on startup instead of being
// papered over.

import { invoke } from "@tauri-apps/api/core";
import { isTauriRuntime } from "./backend";

export interface AutostartStatus {
  /** False in a development build: it never touches the operator's own login. */
  supported: boolean;
  /** What the operating system has, not what the app asked for. */
  enabled: boolean;
  command: string | null;
  error: string | null;
}

export const UNSUPPORTED: AutostartStatus = {
  supported: false,
  enabled: false,
  command: null,
  error: null,
};

export async function readAutostart(): Promise<AutostartStatus> {
  if (!isTauriRuntime()) return UNSUPPORTED;
  try {
    return await invoke<AutostartStatus>("autostart_status");
  } catch {
    return {
      ...UNSUPPORTED,
      supported: true,
      error: "autostart_registry_failed",
    };
  }
}

export async function writeAutostart(
  enabled: boolean,
): Promise<{ status: AutostartStatus; error: string | null }> {
  if (!isTauriRuntime())
    return { status: UNSUPPORTED, error: "autostart_unavailable" };
  try {
    return {
      status: await invoke<AutostartStatus>("set_autostart", { enabled }),
      error: null,
    };
  } catch (error) {
    // Ask the machine what it has now: the failed write must not be shown as a success. The raw
    // reason travels back and the caller maps it once (a mapped message would lose its code).
    return { status: await readAutostart(), error: rawError(error) };
  }
}

export type AutostartDecision =
  "register" | "unregister" | "none" | "unsupported";

/**
 * What a launch owes the machine: the default is on, so a fresh installation (no registry value
 * yet) registers itself, while an explicit "off" removes an entry that is still there.
 */
export function autostartDecision(
  setting: boolean,
  status: AutostartStatus | null,
): AutostartDecision {
  if (!status || !status.supported || status.error) return "unsupported";
  if (setting && !status.enabled) return "register";
  if (!setting && status.enabled) return "unregister";
  return "none";
}

/** Run the startup reconciliation once: returns the state the machine ends up with. */
export async function reconcileAutostart(
  setting: boolean,
): Promise<AutostartStatus> {
  const status = await readAutostart();
  const decision = autostartDecision(setting, status);
  if (decision === "register" || decision === "unregister") {
    const result = await writeAutostart(decision === "register");
    return result.status;
  }
  return status;
}

export function rawError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function autostartError(error: unknown): string {
  const text = rawError(error);
  if (text.includes("autostart_unsupported"))
    return "Эта сборка не управляет автозапуском (доступно в установленном приложении).";
  if (text.includes("autostart_not_removed"))
    return "Windows не подтвердил удаление записи автозапуска.";
  if (text.includes("autostart_not_applied"))
    return "Windows не подтвердил запись автозапуска.";
  if (text.includes("autostart_unavailable"))
    return "Автозапуск доступен только в установленном приложении.";
  return "Не удалось изменить автозапуск Windows.";
}

export function autostartLabel(status: AutostartStatus): string {
  if (!status.supported) return "Недоступно";
  if (status.error) return "Состояние неизвестно";
  return status.enabled ? "Включён" : "Выключен";
}
