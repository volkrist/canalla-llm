import type { RecoveryAction } from "./status";

/** Deterministic presentation categories. They never carry provider internals. */
export type ErrorCategory =
  | "configuration"
  | "network"
  | "provider"
  | "compute"
  | "model_startup"
  | "computer"
  | "web"
  | "tor"
  | "memory"
  | "authentication"
  | "unknown";

export const CATEGORY_TITLE: Record<ErrorCategory, string> = {
  configuration: "Нужна настройка",
  network: "Сеть",
  provider: "Провайдер",
  compute: "Compute",
  model_startup: "Запуск модели",
  computer: "Компьютер",
  web: "Веб",
  tor: "Tor",
  memory: "Память",
  authentication: "Вход",
  unknown: "Ошибка",
};

const CODE_CATEGORY: Record<string, ErrorCategory> = {
  not_configured: "configuration",
  runpod_auth: "configuration",
  runpod_balance: "configuration",
  runpod_invalid_request: "configuration",
  llm_key_missing: "configuration",
  volume_mismatch: "configuration",
  price_limit: "configuration",
  gpu_unavailable: "compute",
  session_budget: "configuration",
  COMPUTE_BUDGET_REACHED: "configuration",
  runpod_unavailable: "network",
  runpod_timeout: "network",
  runpod_rate_limit: "network",
  connection_failed: "network",
  connection_auth_failed: "network",
  malformed_response: "provider",
  not_found: "provider",
  create_unknown: "compute",
  multiple_compute: "compute",
  external_compute: "compute",
  price_changed: "compute",
  price_violation: "compute",
  no_compatible_gpu: "compute",
  startup_failed: "model_startup",
  startup_timeout: "model_startup",
  model_mismatch: "model_startup",
  host_offline: "computer",
  device_auth: "computer",
  web_not_configured: "web",
  provider_unavailable: "web",
  provider_timeout: "web",
  rate_limited: "web",
  provider_auth: "web",
  provider_forbidden: "web",
  billing_required: "web",
  tor_unavailable: "tor",
  tor_not_configured: "tor",
  tor_route_violation_blocked: "tor",
  memory_unavailable: "memory",
  auth_required: "authentication",
  session_expired: "authentication",
};

export function categorize(code: string | null | undefined): ErrorCategory {
  if (!code) return "unknown";
  return CODE_CATEGORY[code] || "unknown";
}

export interface ErrorCard {
  category: ErrorCategory;
  title: string;
  message: string;
  code: string | null;
  action: RecoveryAction | null;
  retryable: boolean;
}

/**
 * One presentation for every failure. The user message stays the product message;
 * the raw code is only offered as a diagnostic detail.
 */
export function errorCard(input: {
  code?: string | null;
  message?: string | null;
  action?: RecoveryAction | null;
  recoverable?: boolean;
  fallback?: ErrorCategory;
}): ErrorCard {
  const category = input.code
    ? categorize(input.code)
    : input.fallback || "unknown";
  return {
    category,
    title: CATEGORY_TITLE[category],
    message: input.message || "Не удалось выполнить действие.",
    code: input.code || null,
    action: input.action ?? null,
    retryable: Boolean(input.recoverable),
  };
}

/**
 * What a recovery action actually does. Every branch reuses machinery that already
 * exists: the settings dialog, the device loop, the compute panel (with its own
 * confirmation), or a plain re-read of the authoritative snapshot.
 */
export function recoveryPlan(action: RecoveryAction): {
  kind: "settings" | "device" | "compute" | "refresh";
  event: string | null;
} {
  if (action === "configure") return { kind: "settings", event: null };
  if (action === "reconnect") {
    return { kind: "device", event: "alex-host-jobs" };
  }
  if (action === "stop" || action === "cancel_search") {
    return { kind: "compute", event: "alex-open-compute" };
  }
  return { kind: "refresh", event: null };
}
