import { useState } from "react";
import type { Api } from "../lib/api";
import {
  familyOf,
  isPendingConfirmation,
  summarizeFamily,
  toolErrors,
  toolStates,
  type ToolRun,
} from "../lib/tools";

function formatCost(run: ToolRun) {
  if (run.cost_actual != null)
    return ` · фактически $${Number(run.cost_actual).toFixed(4)}`;
  if (run.cost_estimate != null)
    return ` · оценка $${Number(run.cost_estimate).toFixed(4)}`;
  return "";
}

function RunDetails({
  run,
  busy,
  error,
  onConfirm,
}: {
  run: ToolRun;
  busy: string | null;
  error?: string;
  onConfirm: (id: string, allow: boolean) => void;
}) {
  return (
    <div>
      <p>
        {run.tool_name} · {toolStates[run.status] || run.status}
        {formatCost(run)}
        {run.origin === "server_policy" ? " · политика сервера" : ""}
      </p>
      {run.error_code && run.error_code !== error && (
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
          <h3>Alex LLM хочет выполнить внешнее действие</h3>
          {Object.entries(run.input_summary).map(([key, value]) => (
            <p key={key}>
              <strong>{key}:</strong> {value}
            </p>
          ))}
          <p>
            Провайдер: {run.provider}. Разрешение относится только к этому
            действию.
          </p>
          <button
            type="button"
            disabled={busy === run.id}
            onClick={() => onConfirm(run.id, true)}
          >
            Разрешить один раз
          </button>
          <button
            type="button"
            disabled={busy === run.id}
            onClick={() => onConfirm(run.id, false)}
          >
            Отменить действие
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
  onConfirm,
}: {
  family: "web" | "tor" | "computer";
  runs: ToolRun[];
  busy: string | null;
  error?: string;
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
  const web = rest.filter((run) => familyOf(run) === "web");
  const tor = rest.filter((run) => familyOf(run) === "tor");
  const computer = rest.filter((run) => familyOf(run) === "computer");
  const other = rest.filter((run) => familyOf(run) === "other");
  if (!runs.length && !error && !state) return null;
  return (
    <section className="tool-activity" aria-label="Работа инструментов">
      {state && <p role="status">{toolStates[state] || state}</p>}
      {error && (
        <p role="status">
          {toolErrors[error] ||
            "Поиск в интернете не удался. Ответ может быть без web-источников."}
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
        onConfirm={confirm}
      />
      <CollapsedFamily
        family="tor"
        runs={tor}
        busy={busy}
        error={error}
        onConfirm={confirm}
      />
      <CollapsedFamily
        family="computer"
        runs={computer}
        busy={busy}
        error={error}
        onConfirm={confirm}
      />
      {other.map((run) => (
        <RunDetails
          key={run.id}
          run={run}
          busy={busy}
          error={error}
          onConfirm={confirm}
        />
      ))}
      {failure && <p role="alert">{failure}</p>}
    </section>
  );
}
