import { useEffect, useRef, useState } from "react";
import type { Api } from "../lib/api";

interface Preferences {
  selection: "automatic" | "manual";
  min_vram_gb: number;
  max_hourly_price: number;
  session_budget: number;
  auto_stop_minutes: number;
  auto_search: boolean;
  search_interval: number;
}
interface GPU {
  id: string;
  name: string;
  vram_gb: number;
  hourly_rate: number;
  availability: string;
  selectable: boolean;
  reason: string | null;
}
interface Quote {
  quote_id: string;
  expires_at: string;
  options: GPU[];
  preferences: Preferences;
  selected_gpu_id?: string;
}
interface Status {
  configured: boolean;
  state: string;
  can_control: boolean;
  message: string | null;
  quote_id: string | null;
  server_now: string;
  next_search_at: string | null;
  can_cancel_search: boolean;
  active_generations: number;
  datacenter: string;
  session: null | {
    id: string;
    gpu_type: string;
    hourly_rate: number;
    billable_seconds: number;
    estimated_cost: number;
    pending_stop: boolean;
    managed: boolean;
    started_at: string | null;
    ready_at: string | null;
    stop_reason: string | null;
  };
}
const labels: Record<string, string> = {
  not_configured: "AI не настроен",
  offline: "AI выключен",
  stopped: "AI остановлен",
  searching: "Поиск GPU",
  no_gpu: "Нет подходящих GPU",
  gpu_found: "GPU найден",
  creating: "Создание Pod",
  create_unknown: "Проверка результата создания",
  starting_pod: "Запуск Pod",
  starting_environment: "Подготовка окружения",
  mounting_storage: "Подключение хранилища",
  starting_llm: "Запуск LLM",
  loading_model: "Загрузка модели",
  ready: "AI готов",
  health_unknown: "Проверка состояния AI",
  generating: "Генерация",
  stopping: "Остановка AI",
  error: "Ошибка AI",
  external_compute: "Обнаружен существующий Pod",
};
const defaults: Preferences = {
  selection: "automatic",
  min_vram_gb: 48,
  max_hourly_price: 1.2,
  session_budget: 3,
  auto_stop_minutes: 10,
  auto_search: false,
  search_interval: 30,
};
const money = (value: number) => `$${Number(value).toFixed(3)}`;

export default function ComputePanel({
  api,
  technical,
}: {
  api: Api;
  technical: boolean;
}) {
  const [status, setStatus] = useState<Status | null>(null);
  const [preferences, setPreferences] = useState<Preferences>(defaults);
  const [open, setOpen] = useState(false);
  const [quote, setQuote] = useState<Quote | null>(null);
  const [selected, setSelected] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirmStop, setConfirmStop] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const locked = useRef(false);
  const key = useRef(crypto.randomUUID());
  useEffect(() => {
    const show = () => setOpen(true);
    window.addEventListener("alex-open-compute", show);
    return () => window.removeEventListener("alex-open-compute", show);
  }, []);
  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        const next = await api.json<Status>("/compute/status");
        if (!cancelled) setStatus(next);
      } catch {
        if (!cancelled) setStatus(null);
      }
    };
    void refresh();
    void api
      .json<Preferences>("/compute/preferences")
      .then((value) => {
        if (!cancelled) setPreferences(value);
      })
      .catch(() => {});
    const timer = setInterval(() => void refresh(), 3000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [api]);
  useEffect(() => {
    if (open) dialog.current?.showModal();
  }, [open]);
  async function run(action: () => Promise<void>) {
    if (locked.current) return;
    locked.current = true;
    setBusy(true);
    setError("");
    try {
      await action();
      setStatus(await api.json<Status>("/compute/status"));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Операция не выполнена");
    } finally {
      setBusy(false);
      locked.current = false;
    }
  }
  async function search() {
    await run(async () => {
      await api.json("/compute/preferences", {
        method: "PUT",
        body: JSON.stringify(preferences),
      });
      const found = await api.json<Quote & { existing?: boolean }>(
        "/compute/search",
        { method: "POST", body: JSON.stringify(preferences) },
      );
      if (found.existing) {
        setOpen(false);
        return;
      }
      setQuote(found);
      setSelected(found.selected_gpu_id || "");
      key.current = crypto.randomUUID();
    });
  }
  const gpu = quote?.options.find((item) => item.id === selected);
  return (
    <section className="compute-panel" aria-label="AI Compute">
      <div className="compute-summary">
        <span
          className={`tiny-dot ${status?.state === "ready" ? "ready" : ""}`}
        />
        <strong>
          {status ? labels[status.state] || status.state : "Compute недоступен"}
        </strong>
        {status?.session && (
          <span>
            {status.session.gpu_type} · {money(status.session.hourly_rate)}/ч ·{" "}
            {Math.floor(status.session.billable_seconds / 60)} мин · ≈
            {money(status.session.estimated_cost)}
          </span>
        )}
        <button disabled={!status || busy} onClick={() => setOpen(true)}>
          AI / Compute
        </button>
        {status?.session && status.can_control && (
          <button
            disabled={busy}
            onClick={() => {
              setConfirmStop(true);
              setOpen(true);
            }}
          >
            Остановить AI
          </button>
        )}
      </div>
      {status?.session?.pending_stop && (
        <p role="status">Остановка после текущего ответа</p>
      )}
      {status?.state === "searching" && (
        <p>
          Следующая проверка:{" "}
          {status.next_search_at
            ? new Date(status.next_search_at).toLocaleTimeString("ru-RU")
            : "ожидание"}
        </p>
      )}
      {open && (
        <dialog
          className="settings-dialog compute-dialog"
          ref={dialog}
          onCancel={() => {
            setOpen(false);
            setConfirmStop(false);
          }}
        >
          <div className="dialog-title">
            <h2>AI / Compute</h2>
            <button
              onClick={() => {
                setOpen(false);
                setConfirmStop(false);
              }}
            >
              Закрыть
            </button>
          </div>
          <p>
            Чат работает в mock-режиме. GPU управляется отдельно. Хранилище
            остаётся в {status?.datacenter || "US-TX-3"}.
          </p>
          {!status?.configured && (
            <p className="muted">
              RunPod Not configured. Администратору нужно добавить
              RUNPOD_API_KEY в backend .env.
            </p>
          )}
          {status && !status.can_control && (
            <p>Запуск и остановка доступны администратору.</p>
          )}
          {status?.message && <p role="status">{status.message}</p>}
          {confirmStop ? (
            <div>
              <h3>Остановить compute?</h3>
              <p>
                {status?.active_generations
                  ? "Pod будет освобождён после текущего ответа."
                  : "Compute будет освобождён сейчас."}{" "}
                Network Volume сохранится.
              </p>
              {!status?.session?.managed && (
                <p>
                  Это существующий Pod вне управления Alex. Остановка требует
                  прав администратора.
                </p>
              )}
              <button
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    await api.json("/compute/stop", {
                      method: "POST",
                      body: JSON.stringify({
                        after_generation: true,
                        confirm_external: !status?.session?.managed,
                      }),
                    });
                    setConfirmStop(false);
                    setOpen(false);
                  })
                }
              >
                Подтвердить остановку
              </button>
              <button onClick={() => setConfirmStop(false)}>Отмена</button>
            </div>
          ) : (
            <>
              <div className="compute-fields">
                <label>
                  Выбор GPU
                  <select
                    value={preferences.selection}
                    onChange={(e) => {
                      setPreferences({
                        ...preferences,
                        selection: e.target.value as Preferences["selection"],
                      });
                      setQuote(null);
                    }}
                  >
                    <option value="automatic">
                      Автоматически · самая дешёвая
                    </option>
                    <option value="manual">Вручную</option>
                  </select>
                </label>
                {(
                  [
                    ["min_vram_gb", "Минимум VRAM, GB"],
                    ["max_hourly_price", "Максимум $/час"],
                    ["session_budget", "Бюджет сессии, $"],
                  ] as const
                ).map(([field, label]) => (
                  <label key={field}>
                    {label}
                    <input
                      type="number"
                      min={field === "min_vram_gb" ? 48 : 0.01}
                      step={field === "min_vram_gb" ? 1 : 0.01}
                      value={preferences[field]}
                      onChange={(e) => {
                        setPreferences({
                          ...preferences,
                          [field]: Number(e.target.value),
                        });
                        setQuote(null);
                      }}
                    />
                  </label>
                ))}
                <label>
                  Автоостановка
                  <select
                    value={preferences.auto_stop_minutes}
                    onChange={(e) => {
                      setPreferences({
                        ...preferences,
                        auto_stop_minutes: Number(e.target.value),
                      });
                      setQuote(null);
                    }}
                  >
                    {[5, 10, 15, 30, 0].map((n) => (
                      <option key={n} value={n}>
                        {n ? `${n} мин без запросов` : "Никогда"}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={preferences.auto_search}
                    onChange={(e) => {
                      setPreferences({
                        ...preferences,
                        auto_search: e.target.checked,
                      });
                      setQuote(null);
                    }}
                  />{" "}
                  Повторять поиск каждые 30 секунд
                </label>
              </div>
              <button
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    await api.json("/compute/preferences", {
                      method: "PUT",
                      body: JSON.stringify(preferences),
                    });
                  })
                }
              >
                Сохранить параметры compute
              </button>
              <button
                disabled={
                  busy ||
                  !status?.configured ||
                  !status.can_control ||
                  !!status.session
                }
                onClick={() => void search()}
              >
                {busy ? "Проверка…" : "Найти GPU"}
              </button>
              {status?.quote_id && !quote && (
                <button
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      const found = await api.json<Quote>(
                        `/compute/quotes/${status.quote_id}`,
                      );
                      setQuote(found);
                      setSelected(
                        found.preferences.selection === "automatic"
                          ? found.options.find((item) => item.selectable)?.id ||
                              ""
                          : "",
                      );
                    })
                  }
                >
                  Показать найденные GPU
                </button>
              )}
              {status?.can_cancel_search && status.state === "searching" && (
                <button
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      await api.json("/compute/search/cancel", {
                        method: "POST",
                      });
                      setQuote(null);
                    })
                  }
                >
                  Отменить поиск
                </button>
              )}
              {quote && (
                <>
                  <table className="gpu-table">
                    <thead>
                      <tr>
                        <th>GPU</th>
                        <th>VRAM</th>
                        <th>$/час</th>
                        <th>Доступность</th>
                      </tr>
                    </thead>
                    <tbody>
                      {quote.options.map((item) => (
                        <tr key={item.id}>
                          <td>
                            <label>
                              <input
                                type="radio"
                                name="gpu"
                                checked={selected === item.id}
                                disabled={
                                  !item.selectable ||
                                  preferences.selection === "automatic"
                                }
                                onChange={() => setSelected(item.id)}
                              />
                              {item.name}
                            </label>
                          </td>
                          <td>{item.vram_gb} GB</td>
                          <td>{money(item.hourly_rate)}</td>
                          <td>
                            {item.reason === "price_limit"
                              ? "Выше лимита"
                              : item.reason === "insufficient_vram"
                                ? "Мало VRAM"
                                : !item.selectable
                                  ? "Нет в наличии"
                                  : "Доступна"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {gpu && (
                    <div className="cost-confirm">
                      <h3>Подтверждение платного запуска</h3>
                      <p>
                        {gpu.name} · {gpu.vram_gb} GB · {status?.datacenter}
                      </p>
                      <p>
                        {money(gpu.hourly_rate)}/час ·{" "}
                        {money(gpu.hourly_rate / 60)}/мин
                      </p>
                      <p>
                        10 мин ≈ {money(gpu.hourly_rate / 6)} · 30 мин ≈{" "}
                        {money(gpu.hourly_rate / 2)} · 60 мин ≈{" "}
                        {money(gpu.hourly_rate)}
                      </p>
                      <p>
                        Хранилище оплачивается отдельно. Текущий тариф API не
                        сообщает. Расчёт compute — оценка, не счёт RunPod.
                      </p>
                      <p>
                        Предложение действительно до{" "}
                        {new Date(quote.expires_at).toLocaleTimeString("ru-RU")}
                        .
                      </p>
                      <button
                        className="primary"
                        disabled={
                          busy ||
                          !status?.can_control ||
                          Date.now() >= Date.parse(quote.expires_at)
                        }
                        onClick={() =>
                          void run(async () => {
                            await api.json("/compute/start", {
                              method: "POST",
                              body: JSON.stringify({
                                quote_id: quote.quote_id,
                                gpu_id: gpu.id,
                                idempotency_key: key.current,
                                confirmed: true,
                              }),
                              signal: AbortSignal.timeout(100000),
                            });
                            setQuote(null);
                            setOpen(false);
                          })
                        }
                      >
                        Подтверждаю запуск за {money(gpu.hourly_rate)}/час
                      </button>
                    </div>
                  )}
                </>
              )}
            </>
          )}
          {technical && status?.session && (
            <details>
              <summary>Технические сведения</summary>
              <pre>{JSON.stringify(status.session, null, 2)}</pre>
            </details>
          )}
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
        </dialog>
      )}
    </section>
  );
}
