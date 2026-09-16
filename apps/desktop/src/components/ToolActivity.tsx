import { useState } from "react";
import type { Api } from "../lib/api";
import { toolErrors, toolStates, type ToolRun } from "../lib/tools";

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
      {runs.slice(-8).map((run) => (
        <div key={run.id}>
          <p>
            {run.tool_name} · {toolStates[run.status] || run.status}
            {run.cost_actual != null
              ? ` · фактически $${Number(run.cost_actual).toFixed(4)}`
              : run.cost_estimate != null
                ? ` · оценка $${Number(run.cost_estimate).toFixed(4)}`
                : ""}
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
                onClick={() => void confirm(run.id, true)}
              >
                Разрешить один раз
              </button>
              <button
                type="button"
                disabled={busy === run.id}
                onClick={() => void confirm(run.id, false)}
              >
                Отменить действие
              </button>
            </div>
          )}
        </div>
      ))}
      {failure && <p role="alert">{failure}</p>}
    </section>
  );
}
