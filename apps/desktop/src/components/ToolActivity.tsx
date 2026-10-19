import { useState } from "react";
import type { Api } from "../lib/api";
import {
  familyOf,
  isPendingConfirmation,
  networkLabel,
  summarizeFamily,
  toolErrors,
  toolStates,
  type ToolRun,
} from "../lib/tools";

const EXPLAIN_KEYS = [
  "task",
  "step",
  "reason",
  "action_detail",
  "target",
  "consequences",
  "risk_level",
  "elevation_required",
] as const;

const EXPLAIN_LABELS: Record<(typeof EXPLAIN_KEYS)[number], string> = {
  task: "Задача",
  step: "Шаг",
  reason: "Причина",
  action_detail: "Действие",
  target: "Объект",
  consequences: "Риск",
  risk_level: "Уровень риска",
  elevation_required: "Требуется UAC/admin",
};

function formatCost(run: ToolRun) {
  if (run.cost_actual != null)
    return ` · фактически $${Number(run.cost_actual).toFixed(4)}`;
  if (run.cost_estimate != null)
    return ` · оценка $${Number(run.cost_estimate).toFixed(4)}`;
  return "";
}

function ConfirmationCopy({ run }: { run: ToolRun }) {
  const risk = run.risk_level || run.input_summary.risk_level || "";
  const summary = run.input_summary;
  if (risk === "CRITICAL") {
    return (
      <>
        <h3>Высокий риск</h3>
        <p>
          <strong>Зачем:</strong> {summary.reason}
        </p>
        <p>
          <strong>Точное действие:</strong>{" "}
          {summary.action_detail || run.tool_name}
        </p>
        <p>
          <strong>Affected target:</strong> {summary.target}
        </p>
        <p>
          <strong>Irreversible consequences:</strong> {summary.consequences}
        </p>
      </>
    );
  }
  if (risk === "SENSITIVE") {
    return (
      <>
        <h3>Canalla LLM хочет выполнить чувствительное действие</h3>
        <p>
          <strong>Зачем:</strong> {summary.reason}
        </p>
        <p>
          <strong>Что изменится:</strong>{" "}
          {summary.action_detail || run.tool_name}
        </p>
        <p>
          <strong>Риск:</strong> {summary.consequences}
        </p>
      </>
    );
  }
  return (
    <>
      <h3>Canalla LLM хочет выполнить:</h3>
      <p>{summary.action_detail || summary.target || run.tool_name}</p>
    </>
  );
}

function RunDetails({
  run,
  busy,
  error,
  hideError = false,
  onConfirm,
}: {
  run: ToolRun;
  busy: string | null;
  error?: string;
  hideError?: boolean;
  onConfirm: (id: string, allow: boolean) => void;
}) {
  const risk = run.risk_level || run.input_summary.risk_level || "";
  const allowLabel =
    risk === "CRITICAL"
      ? "Я понимаю риск — разрешить один раз"
      : "Разрешить один раз";
  return (
    <div>
      <p>
        {run.tool_name} · {toolStates[run.status] || run.status}
        {formatCost(run)}
        {run.origin === "server_policy" ? " · политика сервера" : ""}
        {networkLabel(run) ? ` · ${networkLabel(run)}` : ""}
      </p>
      {run.status !== "waiting_confirmation" &&
        run.input_summary.action_detail && (
          <p>{run.input_summary.action_detail}</p>
        )}
      {!hideError && run.error_code && run.error_code !== error && (
        <p>{toolErrors[run.error_code] || "Операция не выполнена"}</p>
      )}
      {run.result_metadata.supplier_stop_confirmed === false && (
        <p role="alert">
          Остановка у провайдера не подтверждена. Проверьте TinyFish.
        </p>
      )}
      {run.status === "waiting_confirmation" && (
        <div
          role="alertdialog"
          aria-label="Подтвердить внешнее действие"
          className="tool-confirm"
        >
          <ConfirmationCopy run={run} />
          {EXPLAIN_KEYS.some((key) => run.input_summary[key]) ? (
            <>
              {EXPLAIN_KEYS.map((key) =>
                run.input_summary[key] ? (
                  <p key={key}>
                    <strong>{EXPLAIN_LABELS[key]}:</strong>{" "}
                    {run.input_summary[key]}
                  </p>
                ) : null,
              )}
              {Object.entries(run.input_summary)
                .filter(
                  ([key]) => !(EXPLAIN_KEYS as readonly string[]).includes(key),
                )
                .map(([key, value]) => (
                  <p key={key}>
                    <strong>{key}:</strong> {value}
                  </p>
                ))}
            </>
          ) : (
            Object.entries(run.input_summary).map(([key, value]) => (
              <p key={key}>
                <strong>{key}:</strong> {value}
              </p>
            ))
          )}
          {networkLabel(run) && <p>{networkLabel(run)}</p>}
          <p>
            Провайдер: {run.provider}. Разрешение относится только к этому
            точному payload. Если команда, путь или аргументы изменятся, нужно
            новое подтверждение.
          </p>
          <button
            type="button"
            disabled={busy === run.id}
            onClick={() => onConfirm(run.id, true)}
          >
            {allowLabel}
          </button>
          <button
            type="button"
            disabled={busy === run.id}
            onClick={() => onConfirm(run.id, false)}
          >
            Отмена
          </button>
        </div>
      )}
    </div>
  );
}

function CollapsedFamily({
  family,
  runs,
  busy,
  error,
  hidden,
  onConfirm,
}: {
  family: "web" | "tor" | "computer";
  runs: ToolRun[];
  busy: string | null;
  error?: string;
  hidden?: Set<string>;
  onConfirm: (id: string, allow: boolean) => void;
}) {
  if (!runs.length) return null;
  return (
    <details className="tool-summary">
      <summary>{summarizeFamily(family, runs)}</summary>
      {runs.map((run) => (
        <RunDetails
          key={run.id}
          run={run}
          busy={busy}
          error={error}
          hideError={hidden?.has(run.id)}
          onConfirm={onConfirm}
        />
      ))}
    </details>
  );
}

export default function ToolActivity({
  api,
  runs,
  state,
  error,
}: {
  api: Api;
  runs: ToolRun[];
  state: string;
  error?: string;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [failure, setFailure] = useState("");
  async function confirm(id: string, allow: boolean) {
    setBusy(id);
    setFailure("");
    try {
      await api.json("/tools/runs/" + id + "/confirm", {
        method: "POST",
        body: JSON.stringify({ allow }),
      });
    } catch (e) {
      setFailure(e instanceof Error ? e.message : "Подтверждение не принято");
    } finally {
      setBusy(null);
    }
  }
  const pending = runs.filter(isPendingConfirmation);
  const rest = runs.filter((run) => !isPendingConfirmation(run));
  // One message per error code: a run's own detail and the global banner must never render the
  // same sentence twice (Playwright strict mode caught the duplicate, and so would a reader).
  // The banner is outside the collapsed groups, so it is the one guaranteed-visible copy.
  const firstError = rest.find((run) => run.error_code)?.error_code;
  const bannerError = error || firstError;
  const seenCodes = new Set<string>();
  const hidden = new Set<string>();
  for (const run of rest) {
    if (!run.error_code) continue;
    if (run.error_code === bannerError || seenCodes.has(run.error_code)) {
      hidden.add(run.id);
    } else {
      seenCodes.add(run.error_code);
    }
  }
  const web = rest.filter((run) => familyOf(run) === "web");
  const tor = rest.filter((run) => familyOf(run) === "tor");
  const computer = rest.filter((run) => familyOf(run) === "computer");
  const other = rest.filter((run) => familyOf(run) === "other");
  if (!runs.length && !error && !state) return null;
  return (
    <section className="tool-activity" aria-label="Работа инструментов">
      {state && <p role="status">{toolStates[state] || state}</p>}
      {bannerError && (
        <p role="status">
          {toolErrors[bannerError] ||
            `Инструмент не выполнен: ${bannerError}. Ответ может быть без web-источников.`}
        </p>
      )}
      {pending.map((run) => (
        <RunDetails
          key={run.id}
          run={run}
          busy={busy}
          error={error}
          onConfirm={confirm}
        />
      ))}
      <CollapsedFamily
        family="web"
        runs={web}
        busy={busy}
        error={error}
        hidden={hidden}
        onConfirm={confirm}
      />
      <CollapsedFamily
        family="tor"
        runs={tor}
        busy={busy}
        error={error}
        hidden={hidden}
        onConfirm={confirm}
      />
      <CollapsedFamily
        family="computer"
        runs={computer}
        busy={busy}
        error={error}
        hidden={hidden}
        onConfirm={confirm}
      />
      {other.map((run) => (
        <RunDetails
          key={run.id}
          run={run}
          busy={busy}
          error={error}
          hideError={hidden.has(run.id)}
          onConfirm={confirm}
        />
      ))}
      {failure && <p role="alert">{failure}</p>}
    </section>
  );
}
