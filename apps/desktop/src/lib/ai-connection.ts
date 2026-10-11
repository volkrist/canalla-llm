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
 *
 * ## The transition contract (A30/A31/A32)
 *
 * A transition («Connecting…») is a **bounded** claim about a *specific attempt*, never a mood the
 * UI can stay in forever. The server may enrich it with these optional fields, all of them read
 * here when present and *derived* when absent, so a Gateway 1.2.0 that sends none of them still
 * renders honestly:
 *
 * | field                    | meaning                                            | fallback |
 * |--------------------------|----------------------------------------------------|----------|
 * | `operation_id`           | the attempt this transition belongs to              | `null`   |
 * | `started_at`             | when the attempt started                            | `observed_at` |
 * | `observed_at`            | when the compute state in this snapshot was observed | `generated_at` |
 * | `startup_deadline`       | the server's own bound for this attempt             | stage budget below |
 * | `last_success_at`        | when the model was last proven ready                | `null`   |
 * | `current_stage`          | the server's own stage name                         | `compute_state` |
 * | `terminal_state`         | a finished attempt's outcome                        | `null`   |
 * | `typed_error`            | the typed reason a terminal attempt ended with      | `detail_code` |
 * | `readiness_valid_until`  | until when the readiness proof may be trusted       | `null`   |
 *
 * Nothing here starts compute: the module is pure and the UI never turns a timer into a Pod.
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
  | "expired"
  | "error";

/** Where a value came from. A derived bound is the client refusing to wait forever; a server bound
 *  is the owner of the lifecycle having proved one. The report and the diagnostics say which. */
export type ValueSource = "server" | "derived";

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
  /** The stage this transition is in, in the user's words, or null when there is no transition. */
  stage: string | null;
  /** When the current stage was first observed (`started_at`, else the snapshot observation). */
  stageSince: string | null;
  /** Whole seconds the stage has been observed for, when a start is known. */
  elapsedSeconds: number | null;
  /** The moment the client will stop promising this transition. */
  deadline: string | null;
  /** Whether that bound came from the server or is the client's own refusal to wait forever. */
  deadlineSource: ValueSource | null;
  /** When the model was last proven ready, when the server reports it. */
  lastSuccessAt: string | null;
  /** A safe, non-secret attempt identity for the expanded diagnostics. */
  operationId: string | null;
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
  "queueing",
  "prefill",
]);

/** The stage words. Seven stages the brief names, plus the compute states the backend already
 *  reports, so a server that sends `current_stage` and one that only sends `compute_state` render
 *  through the same vocabulary. No percentages: a stage is a fact, progress is not. */
export const STAGE_TEXT: Record<string, string> = {
  checking_gateway: "Проверяем Gateway",
  searching: "Ищем GPU",
  gpu_found: "GPU найден",
  creating: "Создаём Pod",
  starting_pod: "Запускаем Pod",
  starting_environment: "Готовим окружение",
  mounting_storage: "Подключаем хранилище",
  starting_llm: "Запускаем модель",
  connecting: "Подключаемся к модели",
  loading_model: "Загружаем модель",
  queueing: "Ожидаем очередь",
  prefill: "Обрабатываем контекст",
  // Task-1: a persistent capacity wait. The server sends `current_stage: "waiting"`; the compute
  // state spelling is accepted too, so both render through the same vocabulary.
  waiting: "Ожидаем свободную GPU",
  waiting_capacity: "Ожидаем свободную GPU",
  generating: "Генерируем ответ",
  ready: "Готово",
};

/** How long a client may promise each stage before it must say the attempt outlived its window.
 *
 * These are the client's own bounds, and they exist for the one case the server cannot cover: a
 * `starting_pod` snapshot that nobody updates any more. They mirror the server-side numbers the
 * product already documents — the 60 s capacity window and the 300 s startup deadline — and the
 * queue admission bound. They are never widened to hours, and a *server* deadline always wins. */
export const STAGE_BUDGET_SECONDS: Record<string, number> = {
  searching: 60,
  gpu_found: 60,
  creating: 300,
  starting_pod: 300,
  starting_environment: 300,
  mounting_storage: 300,
  starting_llm: 300,
  connecting: 300,
  loading_model: 300,
  queueing: 120,
  prefill: 300,
};

export const DEFAULT_STAGE_BUDGET_SECONDS = 300;

/** Stages the client deliberately never bounds.
 *
 * A persistent capacity wait is the user's own choice to keep looking for a free GPU: the Gateway
 * re-attempts on its own backoff with no arbitrary 60/300 s deadline, so the client must not invent
 * one either. The freshness window still applies, so a wait whose snapshot stopped updating is
 * reported honestly (a typed retry) instead of promising forever. */
export const UNBOUNDED_STAGES = new Set(["waiting", "waiting_capacity"]);

/** How old a snapshot may be and still count as «now» for the AI chip. The status poll is 15 s by
 *  default; 90 s leaves room for a slow or hidden window without ever letting a long-dead attempt
 *  keep an amber promise alive. */
export const SNAPSHOT_FRESH_SECONDS = 90;

function verdict(
  state: AiConnectionState,
  code: AiConnectionCode,
  reason: string,
  action: RecoveryAction | null = null,
  extra: Partial<AiConnection> = {},
): AiConnection {
  return {
    state,
    code,
    label: AI_CONNECTION_LABEL[state],
    reason,
    action,
    stage: null,
    stageSince: null,
    elapsedSeconds: null,
    deadline: null,
    deadlineSource: null,
    lastSuccessAt: null,
    operationId: null,
    ...extra,
  };
}

/** `details` as the backend shaped it, without trusting any of it. */
function detailText(
  details: Record<string, unknown>,
  key: string,
): string | null {
  const value = details[key];
  return typeof value === "string" && value ? value : null;
}

function millis(value: string | null): number | null {
  if (!value) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/** What the snapshot says about the current attempt, with the source of every value named. */
export interface AiTransition {
  operationId: string | null;
  stage: string | null;
  stageSource: ValueSource | null;
  stageSince: string | null;
  observedAt: string | null;
  deadline: string | null;
  deadlineSource: ValueSource | null;
  lastSuccessAt: string | null;
  readinessValidUntil: string | null;
  terminalState: string | null;
  typedError: string | null;
}

/** Read the transition contract out of one AI chip's details.
 *
 * A field the server does not send is `null` — never a value invented here. The one derivation the
 * client owns is the deadline, and it is marked `derived` so the diagnostics and the report can
 * say plainly that the client, not the Gateway, refused to wait any longer. */
export function readTransition(
  details: Record<string, unknown>,
  generatedAt: string | null,
): AiTransition {
  const compute = detailText(details, "compute_state");
  const serverStage = detailText(details, "current_stage");
  const stage = serverStage ?? compute;
  const observedAt = detailText(details, "observed_at") ?? generatedAt;
  const serverDeadline =
    detailText(details, "startup_deadline") ??
    detailText(details, "compute_search_deadline");
  const derivedDeadline = deadlineFor(stage, observedAt);
  return {
    operationId: detailText(details, "operation_id"),
    stage,
    stageSource: serverStage ? "server" : stage ? "derived" : null,
    stageSince: detailText(details, "started_at") ?? observedAt,
    observedAt,
    deadline: serverDeadline ?? derivedDeadline,
    deadlineSource: serverDeadline
      ? "server"
      : derivedDeadline
        ? "derived"
        : null,
    lastSuccessAt: detailText(details, "last_success_at"),
    readinessValidUntil: detailText(details, "readiness_valid_until"),
    terminalState: detailText(details, "terminal_state"),
    typedError:
      detailText(details, "typed_error") ?? detailText(details, "detail_code"),
  };
}

/** `observedAt` plus this stage's budget, or `null` when the stage is not a bounded wait. */
function deadlineFor(
  stage: string | null,
  observedAt: string | null,
): string | null {
  if (!stage) return null;
  // A persistent wait has no client-side bound: the server owns the retry schedule (and the
  // freshness window still turns a stopped snapshot into an honest failure).
  if (UNBOUNDED_STAGES.has(stage)) return null;
  const budget = STAGE_BUDGET_SECONDS[stage] ?? DEFAULT_STAGE_BUDGET_SECONDS;
  const start = millis(observedAt);
  if (budget <= 0 || start === null) return null;
  return new Date(start + budget * 1000).toISOString();
}

function stageLabel(stage: string | null): string | null {
  if (!stage) return null;
  return STAGE_TEXT[stage] ?? null;
}

/** Why a transition may or may not still be promised. «No identity» is the honest leftover: the
 *  server never pointed at an attempt, so nothing is being waited on — that is `waiting`, not an
 *  expired attempt. The other two are an attempt that really existed and outlived its window. */
export type TransitionLiveness =
  "live" | "no_identity" | "stale_observation" | "deadline_passed";

function transitionLiveness(
  transition: AiTransition,
  nowMs: number,
  stale: boolean,
  details: Record<string, unknown>,
): TransitionLiveness {
  if (stale) return "stale_observation";
  // The server's own answer about *this* stage wins over any budget the client could derive: a
  // search it says is not running was never a bounded promise, no matter what window the client
  // would otherwise allow itself.
  if (
    transition.stage === "searching" &&
    details.compute_search_active === false
  ) {
    return "no_identity";
  }
  const observed = millis(transition.observedAt);
  if (observed !== null && nowMs - observed > SNAPSHOT_FRESH_SECONDS * 1000) {
    // The snapshot itself is history: nobody has confirmed this attempt for minutes.
    return "stale_observation";
  }
  const deadline = millis(transition.deadline);
  if (deadline !== null) return deadline > nowMs ? "live" : "deadline_passed";
  return "live";
}

export interface AiConnectionInput {
  snapshot: StatusSnapshot | null;
  /** The last `/status` read failed: the snapshot on screen is history, not a health claim. */
  stale: boolean;
  /** The local backend answered its liveness probe. */
  backendReady: boolean;
  /** Injected clock for tests. The UI passes nothing and gets `Date.now()`. */
  now?: number;
  /** The Canalla Cloud state, when the caller knows it. Used only to name the Gateway check as a
   *  stage; it never turns the AI chip green. */
  cloudState?: string | null;
}

/**
 * The one global AI state.
 *
 * Order matters and is deliberate:
 *
 *  * a failed read is never health, no matter what the last snapshot said;
 *  * a configuration that is missing is named as such instead of being polled forever into a
 *    harmless-looking «Проверяем…»;
 *  * only a proven-ready AI is green, and a Pod that exists without a loaded model is not that;
 *  * «Connecting…» is bounded by the attempt's own deadline (server's, else the stage budget), so
 *    a stale `starting_pod` becomes a typed failure instead of an eternal amber.
 */
export function aiConnection({
  snapshot,
  stale,
  backendReady,
  now,
  cloudState = null,
}: AiConnectionInput): AiConnection {
  const nowMs = now ?? Date.now();
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
  const transition = readTransition(details, snapshot.generated_at ?? null);
  const liveness = transitionLiveness(transition, nowMs, stale, details);
  const sinceMs = millis(transition.stageSince);
  const elapsedSeconds =
    sinceMs === null ? null : Math.max(0, Math.round((nowMs - sinceMs) / 1000));
  const stage = stageLabel(transition.stage);
  const trace: Partial<AiConnection> = {
    stage,
    stageSince: transition.stageSince,
    elapsedSeconds,
    deadline: transition.deadline,
    deadlineSource: transition.deadlineSource,
    lastSuccessAt: transition.lastSuccessAt,
    operationId: transition.operationId,
  };
  const stageVerdict = (): AiConnection => {
    if (liveness === "live") {
      return verdict(
        "connecting",
        "starting",
        stage ?? "Подключаем AI",
        null,
        trace,
      );
    }
    if (liveness === "no_identity") {
      // No attempt was ever identified: the honest answer is that readiness is unproven, not that
      // something expired.
      return verdict(
        "disconnected",
        "waiting",
        ai.message || "AI не подтвердил готовность",
        "retry",
        trace,
      );
    }
    return verdict(
      "disconnected",
      "expired",
      ai.message ||
        "Подключение не завершилось за отведённое время. Повторите попытку.",
      "retry",
      trace,
    );
  };
  // The Gateway check itself is a stage worth naming, and it is not a claim about the model.
  const gatewayChecking = (): AiConnection =>
    verdict("connecting", "checking", stageLabel("checking_gateway")!, null, {
      ...trace,
      stage: stageLabel("checking_gateway"),
    });

  switch (ai.state) {
    case "ready": {
      // Green only while the proof is still valid. A server that reports
      // `readiness_valid_until` past its moment gets an honest, non-green answer: a Pod RUNNING
      // is not a model that answered.
      const validUntil = millis(transition.readinessValidUntil);
      if (validUntil !== null && validUntil <= nowMs) {
        return verdict(
          "disconnected",
          "expired",
          "Подтверждение готовности модели устарело",
          "retry",
          trace,
        );
      }
      return verdict(
        "connected",
        "ready",
        compute === "generating" ? "AI отвечает" : "AI готов к диалогу",
        null,
        trace,
      );
    }
    case "starting":
      return stageVerdict();
    case "degraded":
      // A degraded AI is only «connecting» while a real, bounded transition is in flight; a create
      // whose outcome is unknown, or a Pod somebody else brought up, is not a transition to green.
      return compute && STARTING_COMPUTE.has(compute)
        ? stageVerdict()
        : verdict(
            "disconnected",
            "waiting",
            "AI не подтвердил готовность",
            "retry",
            trace,
          );
    case "off":
      return compute === "stopping"
        ? verdict("disconnected", "stopping", "AI останавливается", null, trace)
        : verdict(
            "disconnected",
            "off",
            "AI выключен: GPU не запущен",
            null,
            trace,
          );
    case "not_configured":
      return verdict(
        "disconnected",
        "not_configured",
        "AI не настроен",
        "configure",
        trace,
      );
    case "unavailable":
      // The backend draws the same line: `configured` says whether there is a credential at all,
      // and the two failures need different words and different buttons.
      if (configured === false) {
        return verdict(
          "disconnected",
          "credentials_missing",
          "RunPod не настроен — добавьте API key",
          "configure",
          trace,
        );
      }
      // A Gateway that is unreachable is a Gateway problem, not a dead model: the two are named
      // apart so an outage is never explained as «модель не отвечает».
      return cloudState === "connecting" || cloudState === "unavailable"
        ? gatewayChecking()
        : verdict(
            "disconnected",
            "provider_unavailable",
            ai.message || "Провайдер AI не отвечает",
            "retry",
            trace,
          );
    case "configured":
      // Configured is not healthy: nothing proved that the provider answers.
      return verdict(
        "disconnected",
        "configured_only",
        "Настроено, но готовность не подтверждена",
        null,
        trace,
      );
    case "error":
      return verdict(
        "disconnected",
        "error",
        ai.message || "AI вернул ошибку",
        ai.action ?? "retry",
        trace,
      );
    default:
      return verdict(
        "disconnected",
        "waiting",
        "AI не подтвердил готовность",
        "retry",
        trace,
      );
  }
}

/** The infrastructure rows. §4 of the STEP 4 brief: keep the facts the old badge carried, but
 *  name them for what they are and never let them wear the global word. */
export interface AiInfrastructure {
  backend: string;
  cloud: string;
}

/** The AI rows of the popover: the backend's own whitelisted details, plus the transition facts
 *  the client derived — labelled so a derived bound is never mistaken for a server promise. */
export function aiConnectionRows(
  snapshot: StatusSnapshot | null,
  stale: boolean,
  connection?: AiConnection | null,
): Array<[string, string]> {
  if (stale || !snapshot) return [];
  const ai = snapshot.subsystems?.ai;
  if (!ai) return [];
  const rows = detailRows("ai", ai);
  if (!connection) return rows;
  const push = (label: string, value: string | null) => {
    if (value !== null && value !== "") rows.push([label, value]);
  };
  push("Стадия", connection.stage);
  push(
    "В стадии",
    connection.elapsedSeconds === null
      ? null
      : `${connection.elapsedSeconds} с`,
  );
  push(
    "Ограничение ожидания",
    connection.deadline
      ? `${clockSeconds(connection.deadline)} · ${connection.deadlineSource === "server" ? "от Gateway" : "клиентское"}`
      : null,
  );
  push("Последняя готовность", stampSeconds(connection.lastSuccessAt));
  // The operation id is a safe, non-secret identifier; the JWT, the installation credential and
  // the RunPod secret are never part of it and are never rendered here.
  push("Операция", connection.operationId);
  return rows;
}

function stampSeconds(value: string | null): string | null {
  const at = millis(value);
  if (at === null) return null;
  return new Date(at).toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function clockSeconds(value: string): string {
  const at = millis(value);
  if (at === null) return value;
  return new Date(at).toLocaleTimeString("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
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
