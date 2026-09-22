import { useState } from "react";
import { errorCard } from "../lib/errors";
import {
  ACTION_TEXT,
  CHIP_ORDER,
  CHIP_TITLES,
  STATE_TEXT,
  balanceLine,
  balanceNote,
  chipState,
  detailRows,
  firstProblem,
  gpuRateLine,
  lowBalanceWarning,
  sessionSpendLine,
  type ChipKey,
  type RecoveryAction,
  type StatusSnapshot,
} from "../lib/status";

function firstActionable(snapshot: StatusSnapshot | null) {
  if (!snapshot) return null;
  for (const chip of CHIP_ORDER) {
    const status = snapshot.subsystems[chip];
    if (
      status &&
      status.action &&
      ["error", "degraded", "unavailable"].includes(status.state)
    ) {
      return { chip, status };
    }
  }
  return null;
}

export default function StatusChips({
  snapshot,
  error,
  refreshing,
  onAction,
  compact = false,
}: {
  snapshot: StatusSnapshot | null;
  error: string;
  refreshing: boolean;
  onAction: (action: RecoveryAction, chip: ChipKey) => void;
  compact?: boolean;
}) {
  const [open, setOpen] = useState<ChipKey | null>(null);
  const alert = firstActionable(snapshot) ?? firstProblem(snapshot);
  // In compact mode the computer warning has its own one-line bar and the balance its own thin
  // line, so this section keeps only the chips, their popover and every other alert.
  const showAlert = !!alert && !(compact && alert.chip === "computer");
  const card = showAlert
    ? errorCard({
        code: alert!.status.detail_code,
        message: alert!.status.message,
        action: alert!.status.action,
        recoverable: alert!.status.recoverable,
      })
    : null;
  const details = open ? snapshot?.subsystems[open] : undefined;
  const rows = open ? detailRows(open, details) : [];
  return (
    <section
      className={compact ? "status-bar compact" : "status-bar"}
      aria-label="Состояние Canalla LLM"
    >
      <div className="status-chips">
        {CHIP_ORDER.map((chip) => {
          const state = chipState(snapshot, chip);
          const active = open === chip;
          return (
            <button
              key={chip}
              type="button"
              className={`status-chip state-${state}${active ? " active" : ""}`}
              aria-expanded={active}
              aria-label={`${CHIP_TITLES[chip]}: ${STATE_TEXT[state]}`}
              title={`${CHIP_TITLES[chip]} · ${STATE_TEXT[state]}`}
              onClick={() => setOpen(active ? null : chip)}
            >
              <span className="status-chip-name">{CHIP_TITLES[chip]}</span>
              <span className="status-chip-state">{STATE_TEXT[state]}</span>
            </button>
          );
        })}
      </div>
      {compact ? null : <StatusBilling snapshot={snapshot} />}
      {error ? (
        <div className="status-alert" role="alert" data-testid="status-error">
          <strong>Состояние недоступно</strong>
          <span>{error}</span>
          <button
            type="button"
            className="status-action"
            disabled={refreshing}
            onClick={() => onAction("retry", "ai")}
          >
            {ACTION_TEXT.retry}
          </button>
        </div>
      ) : null}
      {card ? (
        <div
          className={`status-alert${compact ? " compact" : ""} category-${card.category}`}
          role="alert"
          data-testid="status-recovery"
        >
          <strong>
            {CHIP_TITLES[alert!.chip]} · {card.title}
          </strong>
          {compact ? (
            <details className="compact-details">
              <summary>{card.message}</summary>
              {card.code ? <p>Код: {card.code}</p> : null}
            </details>
          ) : (
            <>
              <span>{card.message}</span>
              {card.code ? (
                <span className="status-code">Код: {card.code}</span>
              ) : null}
            </>
          )}
          {card.action ? (
            <button
              type="button"
              className="status-action"
              onClick={() => onAction(card.action!, alert!.chip)}
            >
              {ACTION_TEXT[card.action]}
            </button>
          ) : null}
        </div>
      ) : null}
      {open && details ? (
        <div className="status-detail" data-testid="status-detail">
          <p>{details.message}</p>
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
          {details.action ? (
            <button
              type="button"
              className="status-action"
              onClick={() => onAction(details.action!, open)}
            >
              {ACTION_TEXT[details.action]}
            </button>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

/** The balance and its billing notes, kept as their own thin line under the header. */
export function StatusBilling({
  snapshot,
}: {
  snapshot: StatusSnapshot | null;
}) {
  const balance = snapshot?.balance;
  return (
    <div className="status-billing" role="status">
      <span className="status-balance" data-testid="runpod-balance">
        {balance ? balanceLine(balance) : "RunPod balance: проверяем…"}
      </span>
      <span className="status-balance-note">
        {balance ? balanceNote(balance) : ""}
      </span>
      {balance && gpuRateLine(balance) ? (
        <span className="status-rate" data-testid="gpu-rate">
          {gpuRateLine(balance)}
        </span>
      ) : null}
      {balance && sessionSpendLine(balance) ? (
        <span className="status-spend" data-testid="session-spend">
          {sessionSpendLine(balance)}
        </span>
      ) : null}
      {balance && lowBalanceWarning(balance) ? (
        <span className="status-warn" data-testid="low-balance">
          {lowBalanceWarning(balance)}
        </span>
      ) : null}
    </div>
  );
}
