// Context meter for the composer: types, formatting, thresholds and the debounced
// reader of the backend snapshot.
//
// The numbers come from the backend (`/chats/{key}/context-usage`): it owns both the
// served window (`llm_context_window`, i.e. llama.cpp `--ctx-size`) and the estimate
// over the parts that really enter the prompt. The frontend never invents a limit and
// never measures characters of its own (docs/context-usage.md).

import { useEffect, useRef, useState } from "react";
import type { Api } from "./api";

export type ContextPart = {
  key: string;
  label: string;
  chars: number;
  tokens: number;
};

export type MeasuredPrompt = {
  prompt_tokens: number | null;
  at: string | null;
};

export type ContextUsage = {
  model: string;
  limit_tokens: number;
  used_tokens: number;
  remaining_tokens: number;
  percent: number;
  count_type?: "exact" | "estimated";
  estimated: boolean;
  method: string;
  updated_at?: string;
  parts: ContextPart[];
  measured: MeasuredPrompt | null;
};

export type ContextLevel = "ok" | "warning" | "danger";

export type MeterSnapshot = {
  usage: ContextUsage | null;
  failed: boolean;
};

/** < 70 normal, 70–85 warning, > 85 danger. */
export const CONTEXT_WARNING_PERCENT = 70;
export const CONTEXT_DANGER_PERCENT = 85;

/** How long to wait after the last keystroke before asking the backend again. */
export const CONTEXT_USAGE_DEBOUNCE_MS = 400;

export function contextLevel(percent: number): ContextLevel {
  if (!Number.isFinite(percent)) return "ok";
  if (percent > CONTEXT_DANGER_PERCENT) return "danger";
  if (percent >= CONTEXT_WARNING_PERCENT) return "warning";
  return "ok";
}

export function formatTokens(value: number): string {
  const rounded = Math.max(0, Math.round(Number.isFinite(value) ? value : 0));
  return String(rounded).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
}

export function formatPercent(percent: number): string {
  return `${Math.max(0, Math.round(Number.isFinite(percent) ? percent : 0))}%`;
}

/** Stroke geometry of the ring; the visual fill never exceeds a full circle. */
export function ringDash(
  percent: number,
  radius: number,
): { dash: number; gap: number } {
  const circumference = 2 * Math.PI * radius;
  const filled = Math.min(100, Math.max(0, percent || 0)) / 100;
  const dash = Math.round(circumference * filled * 100) / 100;
  return { dash, gap: Math.round((circumference - dash) * 100) / 100 };
}

export function contextHint(level: ContextLevel): string | null {
  if (level === "danger")
    return "Контекст почти заполнен: старые сообщения или источники могут быть обрезаны.";
  if (level === "warning") return "Контекст заполняется.";
  return null;
}

export function contextSentence(usage: ContextUsage): string {
  return `Context: ${isEstimated(usage) ? "~" : ""}${formatTokens(
    usage.used_tokens,
  )} из ${formatTokens(usage.limit_tokens)} токенов, ${formatPercent(
    usage.percent,
  )}`;
}

/** A live snapshot is an estimate unless the backend says otherwise. */
export function isEstimated(usage: ContextUsage): boolean {
  return (
    (usage.count_type ?? (usage.estimated ? "estimated" : "exact")) !== "exact"
  );
}

/** `~12 480 / 32 768` while estimated, `12 480 / 32 768` when exact. */
export function usedLabel(usage: ContextUsage): string {
  const used = `${isEstimated(usage) ? "~" : ""}${formatTokens(usage.used_tokens)}`;
  return `${used} / ${formatTokens(usage.limit_tokens)}`;
}

export function measuredLine(measured: MeasuredPrompt | null): string | null {
  if (!measured || measured.prompt_tokens === null) return null;
  return `Последний запрос: ${formatTokens(measured.prompt_tokens)} токенов (измерено)`;
}

/**
 * One answer from the snapshot endpoint, reduced to what the meter may show.
 *
 * A failed read keeps no number at all: the previous percent belongs to an older draft, so
 * showing it as the current one would state something the backend did not say. The next
 * successful answer replaces the unavailable state, so the meter recovers on its own.
 */
export function meterState(
  incoming: ContextUsage | null,
  ok: boolean,
): MeterSnapshot {
  if (!ok) return { usage: null, failed: true };
  return { usage: incoming, failed: false };
}

/** The compact state the composer shows while no snapshot is available. */
export const CONTEXT_UNAVAILABLE_LABEL = "Context недоступен";
export const CONTEXT_UNAVAILABLE_DETAIL =
  "Не удалось получить снимок контекста. Показатель вернётся сам.";

/**
 * The preview request. The draft is a body, never a URL: a 32 000-character Cyrillic draft is
 * a legitimate draft (the composer's own limit), and percent-encoding it into a request line
 * is what used to fail long before the product limit was reached.
 */
export function contextUsageRequest(
  chatId: string,
  draft: string,
): { path: string; init: RequestInit } {
  return {
    path: `/chats/${chatId}/context-usage`,
    init: { method: "POST", body: JSON.stringify({ prompt: draft }) },
  };
}

/**
 * Reads the snapshot for the open chat. Debounced, sequence-guarded (a slow answer
 * never overwrites a newer one) and silent on failure: a meter must not raise errors
 * in the composer when the backend is busy — it says "unavailable" instead, and never
 * keeps a stale percent on screen (see `meterState`).
 *
 * The draft travels in the request body: a 32 000-character Cyrillic draft is a legitimate
 * draft, and a request line is not the place for one (it is what used to fail first).
 */
export function useContextUsage(
  api: Api,
  chatId: string | null,
  draft: string,
  options: { enabled?: boolean; refreshKey?: number; delayMs?: number } = {},
): { usage: ContextUsage | null; loading: boolean; failed: boolean } {
  const {
    enabled = true,
    refreshKey = 0,
    delayMs = CONTEXT_USAGE_DEBOUNCE_MS,
  } = options;
  const [usage, setUsage] = useState<ContextUsage | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const sequence = useRef(0);

  useEffect(() => {
    if (!chatId || !enabled) {
      sequence.current += 1;
      setUsage(null);
      setLoading(false);
      setFailed(false);
      return;
    }
    const id = (sequence.current += 1);
    setLoading(true);
    const timer = setTimeout(() => {
      const request = contextUsageRequest(chatId, draft);
      api
        .json<ContextUsage>(request.path, request.init)
        .then((value) => {
          if (sequence.current !== id) return;
          setUsage(meterState(value, true).usage);
          setFailed(false);
        })
        .catch(() => {
          if (sequence.current !== id) return;
          const next = meterState(null, false);
          setUsage(next.usage);
          setFailed(next.failed);
        })
        .finally(() => {
          if (sequence.current === id) setLoading(false);
        });
    }, delayMs);
    return () => clearTimeout(timer);
  }, [api, chatId, draft, enabled, refreshKey, delayMs]);

  return { usage, loading, failed };
}
