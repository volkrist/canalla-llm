import type { AutonomousTask } from "./tools";

/**
 * One status line for the whole task.
 *
 * The screen used to show four unrelated indicators at once — a stream timer, a step counter, a
 * per-family "Завершено" and the task message — and they disagreed. Everything here reads the
 * single backend `phase` (see `app/tools/local/status.py`). A step counter never makes a task
 * look finished, and a finished tool family is never reported as a finished task.
 *
 * The backend remains authoritative when the payload predates these fields: `phaseOf` falls back
 * to the persisted status, never to "completed".
 */

export type TaskTone = "working" | "waiting" | "done" | "error" | "idle";

export interface TaskStatusLine {
  label: string;
  detail: string;
  tone: TaskTone;
  /** Machine phase, so callers can branch without re-parsing Russian text. */
  phase: string;
  /** True only when the backend says the task is over. */
  terminal: boolean;
}

const PHASE_LABEL: Record<string, string> = {
  queued: "Задача принята",
  ready: "Работает",
  planning: "Работает",
  inspecting: "Работает",
  researching: "Работает",
  executing: "Работает",
  verifying: "Работает",
  retrying: "Работает",
  recovering: "Работает",
  conflict: "Нужно решение",
  paused: "Пауза",
  stopping: "Останавливается",
  waiting_permission: "Нужно разрешение",
  waiting_device: "Компьютер недоступен",
  waiting_workspace: "Ожидает рабочую папку",
  waiting_model: "Ожидается ответ модели",
  waiting_resource: "Ждём вычислительные ресурсы",
  interrupted: "Прервано",
  cancelled: "Остановлено",
  failed: "Не удалось завершить",
  partial: "Не удалось завершить",
  completed: "Завершено",
};

const PHASE_TONE: Record<string, TaskTone> = {
  queued: "idle",
  ready: "working",
  planning: "working",
  inspecting: "working",
  researching: "working",
  executing: "working",
  verifying: "working",
  retrying: "working",
  recovering: "working",
  conflict: "waiting",
  paused: "waiting",
  stopping: "waiting",
  waiting_permission: "waiting",
  waiting_device: "waiting",
  waiting_workspace: "waiting",
  waiting_model: "waiting",
  waiting_resource: "waiting",
  interrupted: "error",
  cancelled: "idle",
  failed: "error",
  partial: "error",
  completed: "done",
};

const STATUS_FALLBACK: Record<string, string> = {
  CREATED: "queued",
  PLANNING: "planning",
  READY: "ready",
  INSPECTING: "inspecting",
  RESEARCHING: "researching",
  EXECUTING: "executing",
  VERIFYING: "verifying",
  RETRYING: "retrying",
  RECOVERING: "recovering",
  WAITING_CONFIRMATION: "waiting_permission",
  WAITING_DEVICE: "waiting_device",
  WAITING_WORKSPACE: "waiting_workspace",
  WAITING_LLM: "waiting_model",
  PAUSED: "paused",
  STOPPING: "stopping",
  STOPPED: "cancelled",
  COMPLETED: "completed",
  FAILED: "failed",
  INTERRUPTED: "interrupted",
  CONFLICT: "conflict",
};

export function phaseOf(
  task: Pick<AutonomousTask, "status" | "phase">,
): string {
  const explicit = String(task.phase || "");
  if (explicit) return explicit;
  return STATUS_FALLBACK[task.status] || "ready";
}

export function phaseLabel(phase: string): string {
  return PHASE_LABEL[phase] || "Работает";
}

const EVENT_LABEL: Record<string, string> = {
  TASK_CREATED: "Задача принята",
  PLAN_CREATED: "Составлен план",
  PLAN_UPDATED: "План обновлён",
  STEP_STARTED: "Начат шаг",
  STEP_COMPLETED: "Шаг выполнен",
  STEP_FAILED: "Шаг не выполнен",
  TOOL_REQUESTED: "Запрошен инструмент",
  TOOL_COMPLETED: "Инструмент завершён",
  FILE_CHANGED: "Изменён файл",
  CONFIRMATION_REQUESTED: "Запрошено подтверждение",
  CONFIRMATION_APPROVED: "Подтверждение получено",
  CONFIRMATION_DENIED: "Подтверждение отклонено",
  VERIFY_STARTED: "Начата проверка",
  VERIFY_PASSED: "Проверка пройдена",
  VERIFY_FAILED: "Проверка не пройдена",
  CHECKPOINT_CREATED: "Сохранено состояние",
  RETRY: "Повторная попытка",
  PAUSED: "Пауза",
  RESUMED: "Продолжение",
  STOPPED: "Остановлено",
  COMPLETED: "Задача завершена",
  FAILED: "Задача завершилась ошибкой",
  INTERRUPTED: "Задача прервана",
  WAITING_DEVICE: "Ожидание компьютера",
  WAITING_LLM: "Ожидание модели",
  WAITING_WORKSPACE: "Ожидание рабочей папки",
  CONFLICT: "Файл изменён извне",
  BUDGET_EXHAUSTED: "Исчерпан бюджет",
  BUDGET_UPDATED: "Бюджет обновлён",
};

export function eventLabel(kind: string): string {
  return EVENT_LABEL[kind] || kind;
}

export function idleText(seconds: number): string {
  const value = Math.max(0, Math.round(seconds || 0));
  if (value < 5) return "только что";
  if (value < 60) return `${value} с назад`;
  const minutes = Math.floor(value / 60);
  if (minutes < 60) return `${minutes} мин назад`;
  const hours = Math.floor(minutes / 60);
  return `${hours} ч назад`;
}

/** Seconds since the last backend progress event, never negative. */
export function idleSeconds(
  task: AutonomousTask,
  now: number = Date.now(),
): number {
  if (typeof task.idle_seconds === "number")
    return Math.max(0, Math.round(task.idle_seconds));
  if (!task.last_progress_at) return 0;
  const at = Date.parse(task.last_progress_at);
  if (!Number.isFinite(at)) return 0;
  return Math.max(0, Math.round((now - at) / 1000));
}

function actionSuffix(task: AutonomousTask): string {
  const action = String(task.current_action || "").trim();
  return action ? ` · ${action}` : "";
}

function stepsSuffix(task: AutonomousTask): string {
  const total = task.steps?.length || 0;
  if (!total) return "";
  const done =
    typeof task.completed_count === "number"
      ? task.completed_count
      : task.steps.filter((step) => step.status === "COMPLETED").length;
  return ` · ${done} из ${total} шагов`;
}

function webHint(task: AutonomousTask): string {
  const web = task.web;
  if (!web) return "";
  const found = web.searches + web.tor_searches;
  if (found > 0 && web.pages_opened === 0)
    return " · источники найдены, страницы не прочитаны";
  return "";
}

export function taskStatusLine(
  task: AutonomousTask,
  now: number = Date.now(),
): TaskStatusLine {
  const phase = phaseOf(task);
  const label = phaseLabel(phase);
  const idle = idleSeconds(task, now);
  const terminal =
    phase === "completed" ||
    phase === "failed" ||
    phase === "partial" ||
    phase === "cancelled";
  const steps = task.steps?.length
    ? `${Math.max(0, (task.completed_count ?? 0) || 0)}/${task.steps.length}`
    : "";

  if (phase === "completed") {
    const summary = String(task.completion_summary || "").trim();
    return {
      phase,
      label,
      tone: "done",
      terminal: true,
      detail: summary || `все шаги выполнены${steps ? ` (${steps})` : ""}`,
    };
  }

  if (phase === "partial" || phase === "failed") {
    const reason = String(task.stop_reason || task.error_code || "").trim();
    // A skipped step is not completed work: the partial count is real completions only.
    const done = task.worked_count ?? task.completed_count ?? 0;
    const total = task.steps?.length ?? 0;
    const worked = total ? ` · выполнено ${done} из ${total}` : "";
    return {
      phase,
      label,
      tone: "error",
      terminal: true,
      detail: (reason
        ? `${reason}${worked}`
        : `причина не указана${worked}`
      ).trim(),
    };
  }

  if (phase === "cancelled") {
    return {
      phase,
      label,
      tone: "idle",
      terminal: true,
      detail:
        String(task.stop_reason || "") === "user"
          ? "остановлено вами"
          : String(task.stop_reason || "остановлено") + stepsSuffix(task),
    };
  }

  if (phase === "waiting_permission") {
    return {
      phase,
      label,
      tone: "waiting",
      terminal: false,
      detail: `подтвердите действие${actionSuffix(task)}`,
    };
  }

  if (phase === "waiting_model" || phase === "waiting_resource") {
    const code = String(task.error_code || "").trim();
    return {
      phase,
      label,
      tone: "waiting",
      terminal: false,
      detail: `${idleText(idle)}${code ? ` · ${code}` : ""}`,
    };
  }

  if (task.stalled) {
    return {
      phase,
      label,
      tone: "error",
      terminal: false,
      detail: `нет событий ${idleText(idle)}${actionSuffix(task)}`,
    };
  }

  if (phase === "queued") {
    return {
      phase,
      label,
      tone: "idle",
      terminal: false,
      detail: `план из ${task.steps?.length || 0} шагов${idle ? ` · ${idleText(idle)}` : ""}`,
    };
  }

  return {
    phase,
    label,
    tone: "working",
    terminal: false,
    detail: `${actionSuffix(task).replace(/^ · /, "") || "готовит следующий шаг"}${
      steps ? ` · ${steps}` : ""
    } · последняя активность ${idleText(idle)}${webHint(task)}`,
  };
}
