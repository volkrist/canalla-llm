import {
  ACTION_TEXT,
  chipState,
  detailRows,
  type ChipKey,
  type RecoveryAction,
  type StatusSnapshot,
} from "../lib/status";

/** The computer subsystem needs attention in exactly these states. `off` and
 *  `not_configured` are deliberate choices, not problems, so the bar stays hidden. */
const PROBLEM_STATES = ["error", "degraded", "unavailable"];

/** One line: a long backend sentence is reduced to its first sentence. */
function shortReason(message: string): string {
  const first = message.split(/(?<=[.!?])\s/)[0].trim() || message.trim();
  return first.length > 72 ? `${first.slice(0, 71).trimEnd()}…` : first;
}

export default function ComputerBar({
  snapshot,
  onAction,
}: {
  snapshot: StatusSnapshot | null;
  onAction: (action: RecoveryAction, chip: ChipKey) => void;
}) {
  const status = snapshot?.subsystems.computer;
  const state = chipState(snapshot, "computer");
  const action = status?.action ?? null;
  if (!status || !PROBLEM_STATES.includes(state)) return null;
  const rows = detailRows("computer", status);
  const reason = status.detail_code || shortReason(status.message);
  const full = [
    status.message,
    status.detail_code ? `Код: ${status.detail_code}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <section
      className="compact-bar computer"
      role="status"
      aria-label="Компьютер"
      title={full}
    >
      <span className="compact-dot" />
      <span className="compact-text">
        Компьютер недоступен{reason ? ` · ${reason}` : ""}
      </span>
      {action ? (
        <span className="compact-actions">
          <button
            type="button"
            className="compact-action"
            onClick={() => onAction(action, "computer")}
          >
            {ACTION_TEXT[action]}
          </button>
        </span>
      ) : null}
      <details className="compact-details">
        <summary>Подробности</summary>
        <p>{status.message}</p>
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
        {status.detail_code ? <p>Код: {status.detail_code}</p> : null}
      </details>
    </section>
  );
}
