import { useEffect, useState } from "react";
import type { Api } from "../lib/api";
import { policyMoney } from "../lib/compute";
import { computeLabels } from "./ComputePanel";
import type { LLMStatus } from "../types";

/** `GET /compute/status`: only the fields this bar reads. The dialog keeps the rest. */
interface Preferences {
  min_vram_gb: number;
  max_hourly_price: number;
  gpu_id?: string | null;
}
interface ComputeStatus {
  configured: boolean;
  state: string;
  can_control: boolean;
  datacenter: string;
  active_generations?: number;
  message?: string | null;
  preferences?: Preferences | null;
  search_preferences?: Preferences | null;
  session: {
    gpu_type: string;
    datacenter?: string | null;
    pending_stop?: boolean;
  } | null;
}

/** The bar speaks the same state language as the AI / Compute dialog (one vocabulary, no
 *  second mapping onto the chip words). Used until the compute status has been read. */
const AI_FALLBACK_LABEL: Record<NonNullable<LLMStatus["ai"]>, string> = {
  off: "AI выключен",
  starting: "Загрузка модели",
  ready: "AI готов",
  waiting: "Ожидание AI",
  unavailable: "AI недоступен",
  error: "Ошибка AI",
};

export default function ComputeBar({
  api,
  llm,
  onSettings,
}: {
  api?: Api;
  llm: LLMStatus | null;
  onSettings?: () => void;
}) {
  const [status, setStatus] = useState<ComputeStatus | null>(null);
  const [prefs, setPrefs] = useState<Preferences | null>(null);
  const ai = llm?.ai ?? null;
  // The compute panel polls /compute/status every few seconds; the bar follows its answer, so
  // the state it shows is the same one the panel (and the dialog) act on.
  useEffect(() => {
    const update = (event: Event) => {
      const detail = (event as CustomEvent<ComputeStatus | null>).detail;
      setStatus(detail ?? null);
    };
    window.addEventListener("alex-compute-status", update);
    return () => window.removeEventListener("alex-compute-status", update);
  }, []);
  useEffect(() => {
    if (!api) return;
    let cancelled = false;
    // No timer of its own: the policy is re-read when the AI state really changes.
    void api
      .json<ComputeStatus>("/compute/status")
      .then((next) => {
        if (!cancelled) setStatus(next);
      })
      .catch(() => {
        if (!cancelled) setStatus(null);
      });
    return () => {
      cancelled = true;
    };
  }, [api, ai]);
  const effective = status?.search_preferences || status?.preferences || prefs;
  useEffect(() => {
    if (!api) return;
    let cancelled = false;
    // The policy line needs the owner's own limits when the status answer does not carry them.
    void api
      .json<Preferences>("/compute/preferences")
      .then((next) => {
        if (!cancelled) setPrefs(next);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [api]);
  const session = status?.session ?? null;
  const label = status?.state
    ? computeLabels[status.state] || status.state
    : ai
      ? AI_FALLBACK_LABEL[ai]
      : "Проверяем состояние…";
  // Absent values are omitted, never replaced by a placeholder.
  const parts: string[] = [];
  const datacenter = session?.datacenter || status?.datacenter || null;
  if (datacenter) parts.push(datacenter);
  const gpu =
    session?.gpu_type || effective?.gpu_id || (effective ? "NVIDIA" : null);
  if (gpu) parts.push(gpu);
  const vram = Number(effective?.min_vram_gb);
  if (Number.isFinite(vram) && vram > 0) parts.push(`VRAM ≥${vram} GB`);
  const price = Number(effective?.max_hourly_price);
  if (Number.isFinite(price) && price > 0)
    parts.push(`цена ≤${policyMoney(price)}/ч`);
  const canStart = Boolean(
    status?.configured &&
    status.can_control &&
    !session &&
    status.state !== "searching",
  );
  const canStop = Boolean(status?.can_control && session);
  // The panel owns the quote, the confirmation and the POST; the bar only routes the action.
  function openCompute() {
    if (onSettings) onSettings();
    else window.dispatchEvent(new Event("alex-open-compute"));
  }
  function runAction() {
    window.dispatchEvent(
      new Event(canStop ? "alex-compute-stop" : "alex-compute-start"),
    );
  }
  return (
    <section className="compact-bar compute" aria-label="AI Compute">
      <span className="compact-dot" />
      <span className="compact-text" title={status?.message || undefined}>
        <strong>{label}</strong>
        {parts.length ? <span> · {parts.join(" · ")}</span> : null}
      </span>
      {status?.session?.pending_stop ? (
        <span className="compact-note">Остановка после текущего ответа</span>
      ) : null}
      <span className="compact-actions">
        <button type="button" className="compact-action" onClick={openCompute}>
          AI / Compute
        </button>
        {canStart || canStop ? (
          <button
            type="button"
            className="compact-action"
            title="Запуск и остановка подтверждаются в разделе AI / Compute"
            onClick={runAction}
          >
            {canStop
              ? status?.active_generations
                ? "Остановить после ответа"
                : "Остановить AI"
              : status?.state === "error"
                ? "Повторить"
                : "Запустить AI"}
          </button>
        ) : null}
      </span>
    </section>
  );
}
