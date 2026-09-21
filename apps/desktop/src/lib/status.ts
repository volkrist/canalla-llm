import type { Api } from "./api";

/** The shared vocabulary. A chip never claims more than its subsystem can prove.
 *
 * `configured` exists because an installation can be set up correctly without any
 * proof that the provider answers: configured is not healthy, and it never renders
 * as "Готово".
 */
export type SubsystemState =
  | "ready"
  | "starting"
  | "configured"
  | "off"
  | "not_configured"
  | "unavailable"
  | "error"
  | "degraded";

export type ChipKey = "ai" | "computer" | "web" | "tor" | "memory";
export type RecoveryAction =
  "retry" | "configure" | "reconnect" | "stop" | "cancel_search";

export interface SubsystemStatus {
  state: SubsystemState;
  message: string;
  detail_code: string | null;
  recoverable: boolean;
  action: RecoveryAction | null;
  details: Record<string, unknown>;
}

export interface ActiveSessionCost {
  gpu: string | null;
  hourly_rate_usd: string;
  estimated_usd: string;
  billable_seconds: number;
  managed: boolean;
  started_at: string | null;
  budget_usd: string;
}

export interface BalanceStatus {
  configured: boolean;
  available: boolean;
  balance_usd: string | null;
  account_spend_per_hr: string | null;
  low: boolean;
  low_threshold_usd: string;
  stale: boolean;
  error_code: string | null;
  message: string | null;
  fetched_at: string | null;
  last_success_at: string | null;
  refresh_seconds: number;
  shared_account: boolean;
  read_only: boolean;
  active_session: ActiveSessionCost | null;
}

export interface StatusSnapshot {
  generated_at: string;
  subsystems: Record<ChipKey, SubsystemStatus>;
  balance: BalanceStatus;
}

export const CHIP_ORDER: ChipKey[] = ["ai", "computer", "web", "tor", "memory"];

export const CHIP_TITLES: Record<ChipKey, string> = {
  ai: "AI",
  computer: "Computer",
  web: "Web",
  tor: "Tor",
  memory: "Memory",
};

/** Text is always rendered, so a chip stays readable without colour. */
export const STATE_TEXT: Record<SubsystemState, string> = {
  ready: "Готово",
  starting: "Проверяем…",
  configured: "Настроено",
  off: "Выключено",
  not_configured: "Не настроено",
  unavailable: "Недоступно",
  error: "Ошибка",
  degraded: "Требует внимания",
};

export const ACTION_TEXT: Record<RecoveryAction, string> = {
  retry: "Повторить",
  configure: "Настроить",
  reconnect: "Подключить",
  stop: "Остановить",
  cancel_search: "Отменить поиск",
};

export function fetchStatus(api: Api): Promise<StatusSnapshot> {
  return api.json<StatusSnapshot>("/status");
}

export function chipState(
  snapshot: StatusSnapshot | null,
  chip: ChipKey,
): SubsystemState {
  if (!snapshot) return "starting";
  return snapshot.subsystems[chip]?.state ?? "starting";
}

export function money(value: string | null | undefined, digits = 2): string {
  if (value === null || value === undefined || value === "") return "—";
  const parsed = Number(value);
  return Number.isFinite(parsed) ? `$${parsed.toFixed(digits)}` : "—";
}

export function minutes(seconds: number | null | undefined): string {
  if (!seconds || seconds < 60) return "<1 мин";
  return `${Math.round(seconds / 60)} мин`;
}

function clockTime(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleTimeString("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** A failed read must never be rendered as `$0.00`. */
export function balanceLine(balance: BalanceStatus): string {
  if (!balance.configured) return "RunPod не настроен";
  if (!balance.available) return "Баланс недоступен";
  return `RunPod balance: ${money(balance.balance_usd)}`;
}

export function balanceNote(balance: BalanceStatus): string {
  if (!balance.configured) return "Добавьте RunPod API key в настройках Alex";
  if (!balance.available) {
    return balance.message || "RunPod не вернул баланс";
  }
  if (balance.stale) {
    return `Устарело: последнее удачное обновление ${clockTime(balance.last_success_at)}`;
  }
  return `обновлено ${clockTime(balance.fetched_at)}`;
}

/** Only shown when the controller has real data for the active session. */
export function gpuRateLine(balance: BalanceStatus): string | null {
  const session = balance.active_session;
  if (!session || !session.gpu) return null;
  return `GPU: ${session.gpu} · ${money(session.hourly_rate_usd)}/ч`;
}

export function sessionSpendLine(balance: BalanceStatus): string | null {
  const session = balance.active_session;
  if (!session) return null;
  return `Сессия: ≈${money(session.estimated_usd, 3)} · ${minutes(session.billable_seconds)}`;
}

export function lowBalanceWarning(balance: BalanceStatus): string | null {
  if (!balance.configured || !balance.available || !balance.low) return null;
  return `Низкий баланс: ${money(balance.balance_usd)} (порог ${money(balance.low_threshold_usd)})`;
}

/** Whitelisted detail rows: nothing else from the payload can reach the DOM. */
export function detailRows(
  chip: ChipKey,
  status: SubsystemStatus | undefined,
): Array<[string, string]> {
  if (!status) return [];
  const details = status.details || {};
  const text = (key: string) => {
    const value = details[key];
    if (value === null || value === undefined || value === "") return null;
    if (typeof value === "boolean") return value ? "да" : "нет";
    return String(value);
  };
  const rows: Array<[string, string]> = [];
  const add = (label: string, key: string) => {
    const value = text(key);
    if (value !== null) rows.push([label, value]);
  };
  if (chip === "ai") {
    add("Провайдер", "provider");
    add("Модель", "model");
    add("Compute", "compute_state");
    add("RunPod key", "configured");
  }
  if (chip === "computer") {
    add("Режим", "computer_mode");
    add("Сопряжён", "paired");
    const device = details.device as
      { display_name?: string } | null | undefined;
    if (device?.display_name) rows.push(["Устройство", device.display_name]);
  }
  if (chip === "web") {
    add("Провайдер", "provider");
    add("Поиск", "search_enabled");
    add("Загрузка страниц", "fetch_enabled");
    add("Профиль готовности", "probe");
  }
  if (chip === "tor") {
    add("Режим", "mode");
    add("SOCKS5", "proxy_port");
    add("Порт отвечает", "socks_listening");
    add("Цепь проверена", "verified_chain");
    add("Откат", "fallback");
  }
  if (chip === "memory") {
    add("Память включена", "enabled");
    add("Записей", "items");
    add("Лимит", "max_items");
    add("Извлечение", "retrieval");
  }
  return rows;
}

/** Polling policy: the backend picks the cadence, a hidden window is never eager. */
export function statusDelaySeconds(
  snapshot: StatusSnapshot | null,
  hidden: boolean,
): number {
  const base = snapshot?.balance.refresh_seconds || 15;
  return hidden ? Math.max(base, 30) : base;
}

/** The first hard failure, used for the recovery banner. */
export function firstProblem(
  snapshot: StatusSnapshot | null,
): { chip: ChipKey; status: SubsystemStatus } | null {
  if (!snapshot) return null;
  for (const chip of CHIP_ORDER) {
    const status = snapshot.subsystems[chip];
    if (status && status.state === "error") return { chip, status };
  }
  return null;
}
