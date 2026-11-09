import { useEffect, useState } from "react";
import type { Api } from "../lib/api";
import {
  eventLabel,
  idleText,
  taskStatusLine,
  type TaskStatusLine,
} from "../lib/task-status";
import type { AutonomousTask } from "../lib/tools";

function clock(seconds: number) {
  const value = Math.max(0, seconds || 0);
  const minutes = Math.floor(value / 60);
  const rest = value % 60;
  return `${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
}

function mark(status: string) {
  if (status === "COMPLETED") return "✓";
  if (status === "RUNNING" || status === "WAITING") return "●";
  if (status === "FAILED") return "!";
  if (status === "CANCELLED" || status === "SKIPPED") return "–";
  return "○";
}

function limitLine(task: AutonomousTask) {
  const limit = task.limit;
  if (!limit) return "";
  const code = String(limit.code || "");
  const pages = `${limit.pages ?? "?"} из ${limit.max_pages ?? "?"} страниц`;
  const fetches = `${limit.fetches ?? "?"} из ${limit.max_fetch ?? "?"} загрузок`;
  const searches = `${limit.searches ?? "?"} из ${limit.max_search ?? "?"} поисков`;
  const calls = `${limit.calls ?? "?"} из ${limit.max_calls ?? "?"} вызовов`;
  if (code === "page_limit")
    return `Лимит страниц: ${pages} · ${fetches} · ${searches}`;
  if (code === "search_limit")
    return `Лимит поисков: ${searches} · ${fetches} · ${pages}`;
  if (code === "tool_limit") return `Лимит вызовов инструментов: ${calls}`;
  return `${code}: ${calls}`;
}

function webLine(task: AutonomousTask) {
  const web = task.web;
  if (!web) return "";
  const total = web.searches + web.fetches + web.tor_searches + web.tor_fetches;
  if (!total) return "";
  const parts = [
    `поисков ${web.searches + web.tor_searches}`,
    `загрузок ${web.fetches + web.tor_fetches}`,
    `страниц открыто ${web.pages_opened}`,
  ];
  if (web.pages_requested) parts.push(`запрошено ${web.pages_requested}`);
  if (web.pages_failed) parts.push(`не удалось ${web.pages_failed}`);
  const found = web.searches + web.tor_searches;
  const opened = web.pages_opened;
  const honest =
    found > 0 && opened === 0
      ? " · источники найдены, страницы не прочитаны"
      : "";
  return `Веб: ${parts.join(" · ")}${honest}`;
}

function securitySummary(task: AutonomousTask) {
  const result = task.security;
  if (!result) return "";
  const risk = result.ok
    ? "проблем высокого риска нет"
    : `${result.blocking} проблем высокого риска`;
  const kev =
    result.kev === "unavailable"
      ? " · CISA KEV: недоступно"
      : result.kev?.startsWith("checked:")
        ? ` · CISA KEV: эксплуатируется ${result.kev.split(":")[1]}`
        : "";
  return `Безопасность: ${risk} · файлов ${result.files_scanned} · пакетов ${result.packages_checked} · advisories: ${result.advisories}${kev}`;
}

function SecurityDetails({ task }: { task: AutonomousTask }) {
  const result = task.security;
  if (!result) return null;
  const scope = task.security_scope;
  const fresh = result.fresh !== false;
  return (
    <div className="task-security">
      <p>{securitySummary(task)}</p>
      {scope && (
        <p className="muted">
          Scope: {scope.targets.join(", ")} · {scope.environment}
        </p>
      )}
      {result.findings.length > 0 && (
        <ol className="security-findings">
          {result.findings.slice(0, 12).map((finding, index) => (
            <li
              key={`${finding.rule}-${index}`}
              data-severity={finding.severity}
            >
              <strong>{finding.severity}</strong> · {finding.rule}
              {finding.file ? ` · ${finding.file}` : ""}
              {finding.line ? `:${finding.line}` : ""}
              {finding.advisory ? ` · ${finding.advisory}` : ""}
              {finding.known_exploited ? " · эксплуатируется (CISA KEV)" : ""}
              {finding.advice ? <span> — {finding.advice}</span> : null}
            </li>
          ))}
        </ol>
      )}
      <p className={fresh ? "muted" : "task-limit"}>
        {fresh
          ? "Проверка выполнена после последних изменений."
          : "Нужна повторная проверка: после оценки файлы менялись."}
      </p>
    </div>
  );
}

function budgetsLine(task: AutonomousTask) {
  const budgets = task.budgets;
  const tools = budgets
    ? `${budgets.tools_used} / ${budgets.tools_max} инструментов`
    : `${task.tool_calls_used} / ${task.tool_budget} инструментов`;
  const files = budgets
    ? `${budgets.files_changed} / ${budgets.files_max} файлов`
    : `${task.files_changed} / ${task.file_change_budget} файлов`;
  const seconds = budgets
    ? `${clock(budgets.seconds_used)} / ${clock(budgets.seconds_max)}`
    : `${clock(task.elapsed_runtime)} / ${clock(task.runtime_budget)}`;
  return `${tools} · ${files} · ${seconds}`;
}

interface TimelineEvent {
  id: string;
  kind: string;
  payload: Record<string, string>;
  created_at: string;
}

function Timeline({ events }: { events: TimelineEvent[] }) {
  if (!events.length) return null;
  return (
    <ol className="task-timeline">
      {events.map((item) => {
        const detail =
          item.payload.summary ||
          item.payload.tool ||
          item.payload.path ||
          item.payload.code ||
          "";
        return (
          <li key={item.id}>
            <time dateTime={item.created_at}>
              {new Date(item.created_at).toLocaleTimeString("ru-RU", {
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
              })}
            </time>{" "}
            {eventLabel(item.kind)}
            {detail ? ` · ${detail}` : ""}
          </li>
        );
      })}
    </ol>
  );
}

export default function TaskPanel({
  task,
  busy,
  api,
  onPause,
  onResume,
  onStop,
}: {
  task: AutonomousTask | null;
  busy?: boolean;
  api?: Api;
  onPause: () => void;
  onResume: () => void;
  onStop: () => void;
}) {
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [open, setOpen] = useState(false);
  const taskId = task?.id || "";

  useEffect(() => {
    if (!open || !api || !taskId) return;
    let active = true;
    void api
      .json<{ events?: TimelineEvent[] }>(`/tasks/${taskId}`)
      .then((payload) => {
        if (active) setEvents(payload.events || []);
      })
      .catch(() => {
        if (active) setEvents([]);
      });
    return () => {
      active = false;
    };
  }, [api, open, taskId]);

  if (!task) return null;
  const line: TaskStatusLine = taskStatusLine(task);
  const total = task.steps.length;
  const done =
    typeof task.completed_count === "number"
      ? task.completed_count
      : task.steps.filter((step) => step.status === "COMPLETED").length;
  const idle = task.idle_seconds;
  const showResume = Boolean(task.resume_available) && !line.terminal;
  const showResumeDone = line.terminal && Boolean(task.resume_available);

  return (
    <section
      className="task-panel"
      data-tone={line.tone}
      aria-label="Статус задачи"
    >
      <header className="task-status-line">
        <strong>{line.label}</strong>
        <span className="task-status-detail" role="status">
          {line.detail}
        </span>
        <span className="task-status-clock">{clock(task.elapsed_runtime)}</span>
      </header>
      {limitLine(task) && (
        <p className="task-limit" role="alert">
          {limitLine(task)}
        </p>
      )}
      {securitySummary(task) && (
        <p className="task-security-line" role="status">
          {securitySummary(task)}
        </p>
      )}
      <div className="task-actions">
        {task.status !== "PAUSED" && !line.terminal && (
          <button type="button" disabled={busy} onClick={onPause}>
            Pause
          </button>
        )}
        {(showResume || showResumeDone) && (
          <button type="button" disabled={busy} onClick={onResume}>
            Resume
          </button>
        )}
        {task.status !== "STOPPED" && !line.terminal && (
          <button type="button" disabled={busy} onClick={onStop}>
            Stop
          </button>
        )}
        <button
          type="button"
          className="task-toggle"
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
        >
          {open ? "Скрыть подробности" : "Подробности"}
        </button>
      </div>
      {open && (
        <div className="task-body">
          <p>
            <strong>{task.title || "Задача"}</strong>
          </p>
          <p>{budgetsLine(task)}</p>
          {webLine(task) && <p>{webLine(task)}</p>}
          <SecurityDetails task={task} />
          <p>
            План: {done} из {total} шагов
            {typeof idle === "number"
              ? ` · последняя активность ${idleText(idle)}`
              : ""}
          </p>
          <ol className="task-plan">
            {task.steps.map((step) => (
              <li key={step.id} data-status={step.status}>
                {mark(step.status)} {step.title}
              </li>
            ))}
          </ol>
          <Timeline events={events} />
          {task.completion_summary && <p>{task.completion_summary}</p>}
          {task.error_code && !line.terminal && (
            <p role="alert">Код ошибки: {task.error_code}</p>
          )}
        </div>
      )}
    </section>
  );
}
