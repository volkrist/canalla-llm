import type { Api } from "./api";
import { record, text as payloadText } from "./payload";
import { parseTorDetails, torEndpoint } from "./tor";

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

/** Wording for a subsystem whose starting state is worth naming. Tor is a service that is
 *  coming up, not a value being read, so it says so. */
const CHIP_STATE_TEXT: Partial<
  Record<ChipKey, Partial<Record<SubsystemState, string>>>
> = {
  tor: { starting: "Подключается…" },
};

export function stateText(chip: ChipKey, state: SubsystemState): string {
  return CHIP_STATE_TEXT[chip]?.[state] ?? STATE_TEXT[state];
}

/** What a chip says. Without any snapshot nothing is known yet, so the generic checking text is
 *  used and no subsystem claims to be connecting. */
export function chipText(
  snapshot: StatusSnapshot | null,
  chip: ChipKey,
): string {
  return snapshot
    ? stateText(chip, chipState(snapshot, chip))
    : STATE_TEXT.starting;
}

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

/** A day-and-time stamp for a moment that may be older than today, or `null` so the row is
 *  dropped: an unreadable timestamp is never rendered as a value. */
function stamp(value: string | null): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function booleanText(value: boolean | null): string | null {
  if (value === null) return null;
  return value ? "да" : "нет";
}

/** The device heartbeat is what the backend state already proved; it is never inferred here. */
function heartbeatText(state: SubsystemState): string | null {
  if (state === "ready") return "в норме";
  if (state === "unavailable") return "нет ответа";
  return null;
}

export interface ComputerDevice {
  display_name: string | null;
  platform: string | null;
  last_seen: string | null;
}

/** `subsystems.computer.details`. `paired` and the device are what the backend proved; the mode is
 *  the user's policy and is reported separately. */
export interface ComputerDetails {
  mode: string | null;
  paired: boolean | null;
  device: ComputerDevice | null;
}

export function parseComputerDetails(
  details: Record<string, unknown> | null | undefined,
): ComputerDetails {
  const raw = details || {};
  const device = record(raw.device);
  return {
    mode: payloadText(raw.computer_mode),
    paired: typeof raw.paired === "boolean" ? raw.paired : null,
    device: device
      ? {
          display_name: payloadText(device.display_name),
          platform: payloadText(device.platform),
          last_seen: payloadText(device.last_seen),
        }
      : null,
  };
}

/** A failed read must never be rendered as `$0.00`. */
export function balanceLine(balance: BalanceStatus): string {
  if (!balance.configured) return "RunPod не настроен";
  if (!balance.available) return "Баланс недоступен";
  return `RunPod balance: ${money(balance.balance_usd)}`;
}

export function balanceNote(balance: BalanceStatus): string {
  if (!balance.configured)
    return "Добавьте RunPod API key в настройках Canalla LLM";
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

/** Whitelisted detail rows: nothing else from the payload can reach the DOM.
 *
 * The Computer and Tor rows lead with health and keep the usage policy as its own row, because the
 * two are different things: a healthy service stays healthy while its mode is off. */
export function detailRows(
  chip: ChipKey,
  status: SubsystemStatus | undefined,
): Array<[string, string]> {
  if (!status) return [];
  const details = status.details || {};
  const rows: Array<[string, string]> = [];
  const push = (label: string, value: string | null) => {
    if (value !== null && value !== "") rows.push([label, value]);
  };
  const text = (key: string) => {
    const value = details[key];
    if (value === null || value === undefined || value === "") return null;
    if (typeof value === "boolean") return value ? "да" : "нет";
    return String(value);
  };
  const add = (label: string, key: string) => push(label, text(key));
  if (chip === "ai") {
    add("Провайдер", "provider");
    add("Модель", "model");
    add("Compute", "compute_state");
    add("RunPod key", "configured");
  }
  if (chip === "computer") {
    const computer = parseComputerDetails(details);
    push("Состояние", stateText("computer", status.state));
    push("Режим", computer.mode);
    push(
      "Сопряжён",
      computer.paired === null ? null : computer.paired ? "да" : "нет",
    );
    push("Отклик", heartbeatText(status.state));
    push("Последний отклик", stamp(computer.device?.last_seen ?? null));
    push("Устройство", computer.device?.display_name ?? null);
  }
  if (chip === "web") {
    add("Провайдер", "provider");
    add("Поиск", "search_enabled");
    add("Загрузка страниц", "fetch_enabled");
    add("Профиль готовности", "probe");
  }
  if (chip === "tor") {
    push("Состояние", stateText("tor", status.state));
    const tor = parseTorDetails(details);
    push("Режим", tor.mode);
    push("SOCKS", torEndpoint(tor));
    push("Порт отвечает", booleanText(tor.socks_listening));
    push("Цепь проверена", booleanText(tor.verified_chain));
    push("Метод", tor.method);
    push(
      "Процесс",
      tor.managed === null ? null : tor.managed ? "Управляемый" : "Внешний",
    );
    push("Последняя проверка", stamp(tor.verified_at));
    push("Откат", tor.fallback);
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
