import { useState } from "react";
import {
  aiConnectionRows,
  aiInfrastructure,
  aiConnection,
  type AiConnection,
  type AiConnectionCode,
} from "../lib/ai-connection";
import {
  ACTION_TEXT,
  type RecoveryAction,
  type StatusSnapshot,
} from "../lib/status";

/** Where a disconnected AI can be acted on, in one line, without a button that would start
 *  compute: the status surface is read-only and stays that way (see `recoveryPlan`). */
const HINT: Partial<Record<AiConnectionCode, string>> = {
  off: "Запустить AI можно в управлении compute.",
  stopping: "Остановка идёт; новое состояние появится само.",
  configured_only: "Готовность подтвердится, когда модель ответит.",
};

/**
 * The popover, as its own component: the whole thing is what a test renders, because static markup
 * of a closed badge cannot show it (the same reason `StatusDetail` is exported).
 */
export function AiConnectionDetail({
  connection,
  rows,
  infra,
  onAction,
}: {
  connection: AiConnection;
  rows: Array<[string, string]>;
  infra: { backend: string; cloud: string };
  onAction: (action: RecoveryAction) => void;
}) {
  const hint = HINT[connection.code];
  return (
    <div
      className="status-detail connection-detail"
      data-testid="ai-connection-detail"
    >
      <p>
        <strong>AI: {connection.label}</strong> — {connection.reason}
      </p>
      {rows.length ? (
        <dl>
          {rows.map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {/* The infrastructure the old badge used to call «Connected»: kept, and named for what it
          is. These words never stand in for the global state. */}
      <dl>
        <div>
          <dt>Backend</dt>
          <dd data-testid="ai-connection-backend">{infra.backend}</dd>
        </div>
        <div>
          <dt>Canalla Cloud</dt>
          <dd data-testid="ai-connection-cloud">{infra.cloud}</dd>
        </div>
      </dl>
      {hint ? <p className="connection-hint">{hint}</p> : null}
      {connection.action ? (
        <button
          type="button"
          className="status-action"
          data-testid="ai-connection-action"
          onClick={() => onAction(connection.action!)}
        >
          {connection.action === "configure"
            ? "Настроить AI"
            : ACTION_TEXT[connection.action]}
        </button>
      ) : null}
    </div>
  );
}

/**
 * The global AI badge: green only for an AI that proved it can answer.
 *
 * It is the one element in the header that speaks for the whole product, so it reads AI readiness
 * and nothing else. The infrastructure facts the old badge mixed in — a live backend, a connected
 * Canalla Cloud — are kept, named and shown in the popover as their own rows.
 */
export default function AiConnectionBadge({
  snapshot,
  stale,
  backendReady,
  cloud,
  onAction,
}: {
  snapshot: StatusSnapshot | null;
  stale: boolean;
  backendReady: boolean;
  cloud: { state: string | null; enrolled: boolean };
  onAction: (action: RecoveryAction) => void;
}) {
  const [open, setOpen] = useState(false);
  const connection = aiConnection({ snapshot, stale, backendReady });
  // A stale snapshot is never a source of detail rows either: it is history.
  const rows = aiConnectionRows(snapshot, stale);
  const infra = aiInfrastructure({
    backendReady,
    cloudState: cloud.state,
    cloudEnrolled: cloud.enrolled,
  });

  return (
    <div className="connection-slot" role="status" aria-live="polite">
      <button
        type="button"
        className={`connection state-${connection.state}`}
        data-testid="ai-connection"
        data-state={connection.state}
        data-code={connection.code}
        aria-expanded={open}
        aria-label={`AI: ${connection.label}. ${connection.reason}`}
        title={`AI · ${connection.label} — ${connection.reason}`}
        onClick={() => setOpen(!open)}
      >
        <span className="tiny-dot" />
        {connection.label}
      </button>
      {open ? (
        <AiConnectionDetail
          connection={connection}
          rows={rows}
          infra={infra}
          onAction={onAction}
        />
      ) : null}
    </div>
  );
}
