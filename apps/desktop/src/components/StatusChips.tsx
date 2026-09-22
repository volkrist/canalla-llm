import { useCallback, useState } from "react";
import type { Api } from "../lib/api";
import { errorCard } from "../lib/errors";
import {
  ACTION_TEXT,
  CHIP_ORDER,
  CHIP_TITLES,
  balanceLine,
  balanceNote,
  chipState,
  chipText,
  detailRows,
  firstProblem,
  gpuRateLine,
  lowBalanceWarning,
  sessionSpendLine,
  type ChipKey,
  type RecoveryAction,
  type StatusSnapshot,
  type SubsystemStatus,
} from "../lib/status";
import { ensureTorService, parseTorDetails, torEnsureText } from "../lib/tor";

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

/** One chip's popover. Health leads and policy is a row of its own, so a healthy service that is
 *  simply not used (`mode: off`) is never rendered as an offline one.
 *
 *  Exported because it is the whole popover: a test renders it opened with a payload, which static
 *  markup of the bar itself cannot do. */
export function StatusDetail({
  chip,
  status,
  onAction,
  onEnsureTor,
  ensuring = false,
}: {
  chip: ChipKey;
  status: SubsystemStatus;
  onAction: (action: RecoveryAction, chip: ChipKey) => void;
  /** The Tor service's own recovery action, absent without an authenticated client. */
  onEnsureTor?: () => void;
  ensuring?: boolean;
}) {
  const rows = detailRows(chip, status);
  // For the Tor service the backend's only action is "retry", which for a service means "become
  // ready now" — not one more status read. The ensure action replaces it, never doubles it.
  const ensure =
    chip === "tor" &&
    onEnsureTor &&
    (status.action === null || status.action === "retry")
      ? onEnsureTor
      : null;
  const action = ensure ? null : status.action;
  return (
    <div className="status-detail" data-testid="status-detail">
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
      {ensure ? (
        <button
          type="button"
          className="status-action"
          data-testid="tor-ensure"
          disabled={ensuring}
          onClick={ensure}
        >
          {torEnsureText(parseTorDetails(status.details))}
        </button>
      ) : null}
      {action ? (
        <button
          type="button"
          className="status-action"
          onClick={() => onAction(action, chip)}
        >
          {ACTION_TEXT[action]}
        </button>
      ) : null}
    </div>
  );
}

export default function StatusChips({
  snapshot,
  error,
  refreshing,
  onAction,
  api,
  compact = false,
}: {
  snapshot: StatusSnapshot | null;
  error: string;
  refreshing: boolean;
  onAction: (action: RecoveryAction, chip: ChipKey) => void;
  /** The authenticated client, for the one chip action that is more than a re-read. */
  api?: Api;
  compact?: boolean;
}) {
  const [open, setOpen] = useState<ChipKey | null>(null);
  const [ensuring, setEnsuring] = useState(false);
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

  const ensureTor = useCallback(async () => {
    if (!api) return;
    setEnsuring(true);
    try {
      // The service answers before the work is done (a cold Tor bootstraps for a minute or more),
      // so the authoritative snapshot is re-read right after instead of waiting on this call.
      await ensureTorService(api);
    } catch {
      // A refused ensure is neither hidden nor invented: the next read shows the real state.
    } finally {
      setEnsuring(false);
    }
    onAction("retry", "tor");
  }, [api, onAction]);

  const act = (action: RecoveryAction, chip: ChipKey) => {
    // Every recovery the bar offers for Tor asks the service itself, then re-reads the snapshot.
    // The other chips keep the plain plan: settings, the device loop, compute, or a re-read.
    if (chip === "tor" && action === "retry" && api) {
      void ensureTor();
      return;
    }
    onAction(action, chip);
  };

  return (
    <section
      className={compact ? "status-bar compact" : "status-bar"}
      aria-label="Состояние Canalla LLM"
    >
      <div className="status-chips">
        {CHIP_ORDER.map((chip) => {
          const state = chipState(snapshot, chip);
          const text = chipText(snapshot, chip);
          const active = open === chip;
          // "No snapshot yet" is not "this subsystem is starting": the bar says so explicitly so a
          // reader (and an acceptance run) can tell the two apart.
          const pending = snapshot === null;
          return (
            <button
              key={chip}
              type="button"
              className={`status-chip state-${state}${active ? " active" : ""}`}
              data-pending={pending ? "true" : undefined}
              aria-expanded={active}
              aria-label={`${CHIP_TITLES[chip]}: ${text}`}
              title={`${CHIP_TITLES[chip]} · ${text}`}
              onClick={() => setOpen(active ? null : chip)}
            >
              <span className="status-chip-name">{CHIP_TITLES[chip]}</span>
              <span className="status-chip-state">{text}</span>
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
            {CHIP_TITLES[alert!.chip] === card.title
              ? card.title
              : `${CHIP_TITLES[alert!.chip]} · ${card.title}`}
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
              onClick={() => act(card.action!, alert!.chip)}
            >
              {ACTION_TEXT[card.action]}
            </button>
          ) : null}
        </div>
      ) : null}
      {open && details ? (
        <StatusDetail
          chip={open}
          status={details}
          onAction={act}
          onEnsureTor={api ? () => void ensureTor() : undefined}
          ensuring={ensuring}
        />
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
