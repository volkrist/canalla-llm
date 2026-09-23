import type { RecoveryAction, StatusSnapshot } from "./status";
import { detailRows } from "./status";

/** The global AI connection: the one place that answers «can Canalla talk to a model right now».
 *
 * The badge used to read the local backend's liveness and print «Connected» for it. That told the
 * user the product was ready while the AI chip an inch away said «Не настроено» and the balance
 * line said «RunPod не настроен» — three statements, two of them false. Backend liveness is a real
 * and useful fact, but it is not AI readiness, so it is reported as its own labelled row instead
 * of being the headline.
 *
 * The backend stays the single owner of the raw state (`compact_ai`, `compute_state`,
 * `configured`). Nothing here invents a second vocabulary: it only decides how much of what the
 * backend already proved may be shown as green.
 */

export type AiConnectionState = "connected" | "connecting" | "disconnected";

/** Why the badge says what it says. Typed so the UI, the tests and the report can name the case. */
export type AiConnectionCode =
  | "checking"
  | "backend_unavailable"
  | "snapshot_stale"
  | "ready"
  | "starting"
  | "not_configured"
  | "credentials_missing"
  | "provider_unavailable"
  | "configured_only"
  | "off"
  | "stopping"
  | "waiting"
  | "error";

export interface AiConnection {
  state: AiConnectionState;
  code: AiConnectionCode;
  /** The headline word. The three words are a fixed vocabulary, not a sentence. */
  label: string;
  /** Why, in the user's language. Never a raw code. */
  reason: string;
  /** What the user can do about it, or null when there is nothing to press (a state that will
   *  resolve on its own, or compute the status surface deliberately never starts). */
  action: RecoveryAction | null;
}

export const AI_CONNECTION_LABEL: Record<AiConnectionState, string> = {
  connected: "Connected",
  connecting: "Connecting…",
  disconnected: "Disconnected",
};

/** Compute states in which a Pod is on its way up. Mirrors the backend's own `STARTING` set in
 *  `app/compute/runtime.py`: a state this list does not know is never called «connecting», it
 *  falls back to the honest «not proven». */
const STARTING_COMPUTE = new Set([
  "searching",
  "gpu_found",
  "creating",
  "starting_pod",
  "starting_environment",
  "mounting_storage",
  "starting_llm",
  "connecting",
  "loading_model",
]);

/** What the transition is doing, in the user's words. Only used while connecting. */
const COMPUTE_STAGE_TEXT: Record<string, string> = {
  searching: "Ищем GPU",
  gpu_found: "GPU найден, запускаем Pod",
  creating: "Создаём Pod",
  starting_pod: "Запускаем Pod",
  starting_environment: "Готовим окружение",
  mounting_storage: "Подключаем хранилище",
  starting_llm: "Запускаем модель",
  connecting: "Подключаемся к модели",
  loading_model: "Загружаем модель",
};

function verdict(
  state: AiConnectionState,
  code: AiConnectionCode,
  reason: string,
  action: RecoveryAction | null = null,
): AiConnection {
  return { state, code, label: AI_CONNECTION_LABEL[state], reason, action };
}

/** `details` as the backend shaped it, without trusting any of it. */
function detailText(
  details: Record<string, unknown>,
  key: string,
): string | null {
  const value = details[key];
  return typeof value === "string" && value ? value : null;
}

export interface AiConnectionInput {
  snapshot: StatusSnapshot | null;
  /** The last `/status` read failed: the snapshot on screen is history, not a health claim. */
  stale: boolean;
  /** The local backend answered its liveness probe. */
  backendReady: boolean;
}

/**
 * The one global AI state.
 *
 * Order matters and is deliberate:
 *
 *  * a failed read is never health, no matter what the last snapshot said;
 *  * a configuration that is missing is named as such instead of being polled forever into a
 *    harmless-looking «Проверяем…»;
 *  * only a proven-ready AI is green, and a Pod that exists without a loaded model is not that.
 */
export function aiConnection({
  snapshot,
  stale,
  backendReady,
}: AiConnectionInput): AiConnection {
  if (stale) {
    return verdict(
      "disconnected",
      "snapshot_stale",
      "Состояние устарело: последнее чтение не удалось",
      "retry",
    );
  }
  if (!snapshot) {
    // Nothing read yet. With a live backend that is a real, bounded read in flight; without one
    // there is nothing to read at all.
    return backendReady
      ? verdict("connecting", "checking", "Проверяем состояние AI")
      : verdict(
          "disconnected",
          "backend_unavailable",
          "Backend не отвечает",
          "retry",
        );
  }

  const ai = snapshot.subsystems?.ai;
  if (!ai) {
    return verdict("disconnected", "error", "Состояние AI недоступно", "retry");
  }

  const details = ai.details || {};
  const compute = detailText(details, "compute_state");
  const configured =
    typeof details.configured === "boolean" ? details.configured : null;
  const stage = compute ? COMPUTE_STAGE_TEXT[compute] : null;

  switch (ai.state) {
    case "ready":
      // The backend only reports this from a model that answered, in either provider mode.
      return verdict(
        "connected",
        "ready",
        compute === "generating" ? "AI отвечает" : "AI готов к диалогу",
      );
    case "starting":
      return verdict("connecting", "starting", stage ?? "Подключаем AI");
    case "degraded":
      // A degraded AI is only «connecting» while a transition is really in flight; a create whose
      // outcome is unknown, or a Pod somebody else brought up, is not a transition to green.
      return compute && STARTING_COMPUTE.has(compute)
        ? verdict("connecting", "starting", stage ?? "Подключаем AI")
        : verdict(
            "disconnected",
            "waiting",
            "AI не подтвердил готовность",
            "retry",
          );
    case "off":
      return compute === "stopping"
        ? verdict("disconnected", "stopping", "AI останавливается")
        : verdict("disconnected", "off", "AI выключен: GPU не запущен");
    case "not_configured":
      return verdict(
        "disconnected",
        "not_configured",
        "AI не настроен",
        "configure",
      );
    case "unavailable":
      // The backend draws the same line: `configured` says whether there is a credential at all,
      // and the two failures need different words and different buttons.
      return configured === false
        ? verdict(
            "disconnected",
            "credentials_missing",
            "RunPod не настроен — добавьте API key",
            "configure",
          )
        : verdict(
            "disconnected",
            "provider_unavailable",
            ai.message || "Провайдер AI не отвечает",
            "retry",
          );
    case "configured":
      // Configured is not healthy: nothing proved that the provider answers.
      return verdict(
        "disconnected",
        "configured_only",
        "Настроено, но готовность не подтверждена",
      );
    case "error":
      return verdict(
        "disconnected",
        "error",
        ai.message || "AI вернул ошибку",
        ai.action ?? "retry",
      );
    default:
      return verdict(
        "disconnected",
        "waiting",
        "AI не подтвердил готовность",
        "retry",
      );
  }
}

/** The infrastructure rows. §4 of the STEP 4 brief: keep the facts the old badge carried, but
 *  name them for what they are and never let them wear the global word. */
export interface AiInfrastructure {
  backend: string;
  cloud: string;
}

/** The AI rows of the popover. A snapshot that is stale or absent has no rows to offer: it is
 *  history, not a statement about the AI. Kept here so the badge and its test read the same rule. */
export function aiConnectionRows(
  snapshot: StatusSnapshot | null,
  stale: boolean,
): Array<[string, string]> {
  if (stale || !snapshot) return [];
  const ai = snapshot.subsystems?.ai;
  return ai ? detailRows("ai", ai) : [];
}

export function aiInfrastructure(input: {
  backendReady: boolean;
  cloudState: string | null;
  cloudEnrolled: boolean;
}): AiInfrastructure {
  const cloud = (() => {
    if (!input.cloudEnrolled) return "Не подключено";
    if (input.cloudState === "connected") return "Подключено";
    if (input.cloudState === "connecting") return "Подключаемся…";
    if (input.cloudState === "protocol_mismatch") return "Версии не совпадают";
    if (input.cloudState === "revoked") return "Доступ отозван";
    if (input.cloudState === "unavailable") return "Недоступно";
    return "Не подключено";
  })();
  return {
    backend: input.backendReady ? "Готов" : "Не отвечает",
    cloud,
  };
}
