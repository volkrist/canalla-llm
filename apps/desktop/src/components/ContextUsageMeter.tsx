// Compact context meter for the composer: a small ring plus the numbers, and a
// breakdown of what consumes the window. Every number comes from the backend
// snapshot; this component only formats and colours it.

import { useState } from "react";
import {
  contextHint,
  contextLevel,
  contextSentence,
  formatPercent,
  formatTokens,
  isEstimated,
  measuredLine,
  ringDash,
  usedLabel,
  type ContextUsage,
} from "../lib/context-usage";

const RING_SIZE = 30;
const RING_STROKE = 3.5;
const RING_RADIUS = (RING_SIZE - RING_STROKE) / 2;

export function ContextBreakdown({
  usage,
  hint,
  measured,
}: {
  usage: ContextUsage;
  hint: string | null;
  measured: string | null;
}) {
  return (
    <div
      className="context-breakdown"
      role="group"
      aria-label="Разбивка контекста"
    >
      <ul>
        {usage.parts.map((part) => (
          <li key={part.key}>
            <span>{part.label}</span>
            <span>{formatTokens(part.tokens)}</span>
          </li>
        ))}
      </ul>
      <p className="context-total">
        <span>Total</span>
        <span>{usedLabel(usage)}</span>
      </p>
      {hint && <p className="context-note">{hint}</p>}
      <p className="context-note">
        {isEstimated(usage)
          ? "Оценка по активному контексту"
          : "Точный размер из ответа модели"}
      </p>
      {measured && <p className="context-note">{measured}</p>}
    </div>
  );
}

export default function ContextUsageMeter({
  usage,
}: {
  usage: ContextUsage | null;
}) {
  const [open, setOpen] = useState(false);
  if (!usage) return null;

  const level = contextLevel(usage.percent);
  const { dash, gap } = ringDash(usage.percent, RING_RADIUS);
  const hint = contextHint(level);
  const measured = measuredLine(usage.measured);

  return (
    <div className={`context-row level-${level}`} data-level={level}>
      <button
        type="button"
        className="context-meter"
        aria-label={contextSentence(usage)}
        aria-expanded={open}
        title={hint ?? "Context"}
        onClick={() => setOpen((value) => !value)}
      >
        <svg
          className="context-ring"
          width={RING_SIZE}
          height={RING_SIZE}
          viewBox={`0 0 ${RING_SIZE} ${RING_SIZE}`}
          aria-hidden="true"
        >
          <circle
            className="context-ring-track"
            cx={RING_SIZE / 2}
            cy={RING_SIZE / 2}
            r={RING_RADIUS}
            fill="none"
            strokeWidth={RING_STROKE}
          />
          <circle
            className="context-ring-value"
            cx={RING_SIZE / 2}
            cy={RING_SIZE / 2}
            r={RING_RADIUS}
            fill="none"
            strokeWidth={RING_STROKE}
            strokeLinecap="round"
            strokeDasharray={`${dash} ${gap}`}
            transform={`rotate(-90 ${RING_SIZE / 2} ${RING_SIZE / 2})`}
          />
        </svg>
        <span className="context-label">Context</span>
        <span className="context-numbers">{usedLabel(usage)}</span>
        <span className="context-percent">{formatPercent(usage.percent)}</span>
      </button>
      {level === "danger" && hint && (
        <span className="context-alert" role="status">
          {hint}
        </span>
      )}
      {open && (
        <ContextBreakdown usage={usage} hint={hint} measured={measured} />
      )}
    </div>
  );
}
