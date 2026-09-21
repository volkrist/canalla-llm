import { useEffect, useSyncExternalStore } from "react";
import type { Api } from "./api";
import { isTauriRuntime } from "./backend";

/** Alex Cloud vocabulary. A label never claims more than the backend proved:
 *  `configured` in the desktop is not the same as a reachable Gateway. */
export type CloudState =
  | "connected"
  | "connecting"
  | "not_connected"
  | "unavailable"
  | "revoked"
  | "protocol_mismatch";

export type CloudDetailCode =
  | "gateway_not_connected"
  | "gateway_unavailable"
  | "gateway_protocol_mismatch"
  | "gateway_auth_failed"
  | "installation_revoked"
  | null;

export type CloudMode = "shared" | "direct";
export type CloudBalanceSource = "gateway" | "runpod_direct";
export type CloudAction = null | "retry" | "configure";

/** `GET /cloud/status` on the local backend. `details` is never rendered: only
 *  whitelisted fields reach the DOM. */
export interface CloudStatus {
  mode: CloudMode;
  state: CloudState;
  message: string;
  detail_code: CloudDetailCode;
  url: string | null;
  default_url: string | null;
  installation_id: string | null;
  enrolled: boolean;
  reachable: boolean;
  protocol_version: number | null;
  balance_source: CloudBalanceSource;
  recoverable: boolean;
  action: CloudAction;
  details: Record<string, unknown>;
}

/** `gateway_status` from the desktop. The installation secret never crosses this
 *  boundary; `installation_id` is a public identifier. */
export interface GatewayStatus {
  configured: boolean;
  url: string | null;
  default_url: string | null;
  installation_id: string | null;
  state: string;
  message: string | null;
}

const CLOUD_LABELS: Record<CloudState, string> = {
  connected: "Подключено",
  connecting: "Подключаемся…",
  not_connected: "Не подключено",
  unavailable: "Недоступно",
  revoked: "Отозвано",
  protocol_mismatch: "Несовместимая версия",
};

export function cloudLabel(state: CloudState): string {
  return CLOUD_LABELS[state] ?? state;
}

/** Shared mode means the Gateway holds the RunPod account, so a local key is
 *  pointless. An unknown mode is not shared: the local key path stays open. */
export function isSharedMode(status: CloudStatus | null): boolean {
  return status?.mode === "shared";
}

/** Shared snapshot. Settings needs the mode and the panel needs the rest, so both
 *  read one object and can never disagree with each other. */
export interface CloudSnapshot extends CloudSnapshotParts {
  /** The last Gateway compute answer. `GET /cloud/status` does not carry the compute
   *  payload, so only `ensure`/`stop` can prove it: `null` means "not known", never
   *  "no Pod". Reading a shared Pod's rate must not become a request of its own. */
  compute: CloudComputeStatus | null;
}

type CloudSnapshotParts = {
  status: CloudStatus | null;
  gateway: GatewayStatus | null;
  /** Set when a read failed: the UI then says the status is unknown instead of
   *  inventing a state. */
  error: string;
};

const EMPTY: CloudSnapshotParts = { status: null, gateway: null, error: "" };

let snapshot: CloudSnapshot = { ...EMPTY, compute: null };
const listeners = new Set<() => void>();
let client: Api | null = null;
let read: Promise<CloudSnapshot> | null = null;
/** Survives the status reads: a running Pod stays a fact until the Gateway says otherwise. */
let compute: CloudComputeStatus | null = null;

function publish(next: CloudSnapshotParts) {
  snapshot = { ...next, compute };
  for (const listener of listeners) listener();
}

/** Only `ensure`/`stop` return the compute payload, so they are the only writers. */
function publishCompute(next: CloudComputeStatus | null) {
  compute = next;
  publish({
    status: snapshot.status,
    gateway: snapshot.gateway,
    error: snapshot.error,
  });
}

export function cloudSnapshot(): CloudSnapshot {
  return snapshot;
}

export function subscribeCloud(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** App builds the single authenticated Api; the props-free panel must not build a
 *  second HTTP layer, so the Settings dialog hands that client over. A new session
 *  (or logout) clears the snapshot instead of carrying it over. */
export function setCloudClient(api: Api | null): void {
  if (api === client && api !== null) return;
  client = api;
  // A new session (or logout) inherits neither the previous status nor its compute answer.
  compute = null;
  publish(EMPTY);
}

export function cloudClient(): Api | null {
  return client;
}

/** Same client, auth header and error behaviour as `fetchStatus`: a failed read
 *  rejects (ApiError) instead of returning a placeholder status. */
export function fetchCloudStatus(
  api: Api | null = client,
): Promise<CloudStatus> {
  if (!api) return Promise.reject(new Error("Нет активной сессии"));
  return api.json<CloudStatus>("/cloud/status");
}

async function invokeCommand<T>(
  command: string,
  args?: Record<string, unknown>,
): Promise<T> {
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<T>(command, args);
}

export function gatewayStatus(): Promise<GatewayStatus> {
  return invokeCommand<GatewayStatus>("gateway_status");
}

export type RestartOutcome = "ok" | "external" | "failed";

/** The owned backend restarts so the new credential takes effect. An external
 *  backend is reported to the user, never restarted: Alex does not own it. */
async function restartBackend(): Promise<RestartOutcome> {
  try {
    await invokeCommand("restart_backend");
    return "ok";
  } catch (error) {
    return String(error) === "backend_not_owned" ? "external" : "failed";
  }
}

async function readCloud(): Promise<CloudSnapshot> {
  let gateway: GatewayStatus | null = snapshot.gateway;
  try {
    gateway = await gatewayStatus();
  } catch {
    // An install without the gateway commands still shows the rest of the app.
  }
  if (!client) {
    // The endpoint is authenticated, so without a session there is nothing to
    // prove, and no state is invented here.
    publish({
      status: null,
      gateway,
      error: "Состояние Canalla Cloud доступно после входа в аккаунт.",
    });
    return snapshot;
  }
  try {
    publish({ status: await fetchCloudStatus(client), gateway, error: "" });
  } catch (error) {
    publish({
      status: null,
      gateway,
      error:
        error instanceof Error ? error.message : "Canalla Cloud недоступен",
    });
  }
  return snapshot;
}

/** One read at a time: the dialog and the panel share the same request. The read is
 *  settled, because a freshly restarted backend still answers `connecting` for a moment
 *  and the panel must not keep that transient label. */
export function refreshCloud(): Promise<CloudSnapshot> {
  if (!read) {
    read = settleCloud().finally(() => {
      read = null;
    });
  }
  return read;
}

/** Enroll this installation. The activation code is sent once and never stored,
 *  logged or echoed back. Enrollment and the backend restart are separate
 *  outcomes: a rejected code is not the same failure as a failed restart. */
export async function enrollGateway(
  url: string,
  activationCode: string,
): Promise<RestartOutcome> {
  await invokeCommand("gateway_enroll", { url, activationCode });
  const restart = await restartBackend();
  // An authoritative read: an answer that was in flight is now stale.
  await settleCloud();
  return restart;
}

/** The restarted backend talks to the Gateway a moment later, so a read can still say
 *  `connecting`. Waiting (bounded) for the first settled answer keeps the panel from showing
 *  a transient label; a read that failed outright is reported as it is. */
async function settleCloud(
  attempts = 12,
  delayMs = 750,
): Promise<CloudSnapshot> {
  let result = await readCloud();
  for (
    let attempt = 0;
    attempt < attempts && result.status?.state === "connecting";
    attempt += 1
  ) {
    await new Promise((resolve) => setTimeout(resolve, delayMs));
    result = await readCloud();
  }
  return result;
}

/** Disconnect this installation from the shared Gateway. */
export async function disconnectGateway(): Promise<RestartOutcome> {
  await invokeCommand("gateway_disconnect");
  const restart = await restartBackend();
  await settleCloud();
  return restart;
}

const ENROLL_ERRORS: Record<string, string> = {
  invalid_url: "Адрес Gateway указан неверно.",
  insecure_gateway_url:
    "Для Gateway нужен HTTPS. HTTP допустим только для localhost.",
  invalid_activation_code: "Код активации не указан или выглядит неверно.",
  activation_code_rejected:
    "Gateway отклонил код активации. Проверьте код и срок его действия.",
  gateway_unreachable:
    "Gateway не отвечает. Проверьте адрес и подключение к сети.",
  gateway_protocol_mismatch:
    "Версия Gateway несовместима с этой версией Canalla LLM.",
};

/** Stable codes become honest Russian text. The activation code is never echoed;
 *  the raw error is only a fallback for a code this build does not know. */
export function enrollError(error: unknown): string {
  const code = String(error);
  return (
    ENROLL_ERRORS[code] || `Не удалось подключиться к Canalla Cloud: ${code}.`
  );
}

/** Shared compute state as reported by the Gateway. The Pod id, the provider URL and the
 *  Pod credential are deliberately absent: only the Gateway can reach llama.cpp. */
export interface CloudComputeSession {
  gpu: string;
  hourly_rate_usd: string;
  budget_usd: string;
  estimated_usd: string;
  billable_seconds: number;
  auto_stop_minutes: number;
  managed: boolean;
  adopted: boolean;
}

export interface CloudComputeStatus {
  state: string;
  ai: string;
  ai_label: string | null;
  message: string | null;
  error_code: string | null;
  detail: string | null;
  managed: boolean;
  adopted: boolean;
  session: CloudComputeSession | null;
  last_session: CloudComputeSession | null;
  idle_deadline: string | null;
}

export type CloudComputeSnapshot = CloudStatus & {
  compute: CloudComputeStatus;
};

/** Ask the Gateway for shared compute. Idempotent: the Gateway decides whether a Pod is
 *  created and never creates a second one, so this is safe to press when AI is already
 *  running. Money limits are server-side and never sent from here. */
export async function ensureCloudCompute(
  taskId?: string,
): Promise<CloudComputeSnapshot> {
  if (!client) throw new Error("Нет активной сессии");
  const result = await client.json<CloudComputeSnapshot>(
    "/cloud/compute/ensure",
    {
      method: "POST",
      body: JSON.stringify(taskId ? { task_id: taskId } : {}),
    },
  );
  publishCompute(result.compute);
  return result;
}

/** Stop shared compute. The Gateway refuses for provider compute Alex does not own. */
export async function stopCloudCompute(): Promise<CloudComputeSnapshot> {
  if (!client) throw new Error("Нет активной сессии");
  const result = await client.json<CloudComputeSnapshot>(
    "/cloud/compute/stop",
    { method: "POST", body: JSON.stringify({}) },
  );
  publishCompute(result.compute);
  return result;
}

/** Human summary of one shared compute session, or null when there is nothing to show. */
export function sessionSummary(
  session: CloudComputeSession | null,
): string | null {
  if (!session) return null;
  const rate = Number(session.hourly_rate_usd);
  const budget = Number(session.budget_usd);
  const spent = Number(session.estimated_usd);
  if (!Number.isFinite(rate) || !Number.isFinite(budget)) return null;
  const parts = [
    `${session.gpu || "GPU"} · $${rate.toFixed(2)}/ч`,
    `бюджет $${budget.toFixed(2)}`,
  ];
  if (Number.isFinite(spent)) parts.push(`израсходовано ~$${spent.toFixed(2)}`);
  return parts.join(" · ");
}

/** The single owner of the cloud read lifecycle for the Settings dialog and the
 *  panel. `api` is registered only when a caller has one (App builds it), so the
 *  props-free panel reuses that client instead of opening a second HTTP layer. */
export function useCloud(api?: Api | null): CloudSnapshot {
  useEffect(() => {
    if (api !== undefined) setCloudClient(api);
    if (isTauriRuntime()) void refreshCloud();
  }, [api]);
  return useSyncExternalStore(subscribeCloud, cloudSnapshot, cloudSnapshot);
}
