import { useEffect, useState } from "react";
import type { Api } from "../lib/api";

export interface EmbeddingState {
  state: string;
  ready: boolean;
  downloaded_bytes: number;
  total_bytes: number | null;
  error: string | null;
  can_cancel: boolean;
  cancel_requested: boolean;
}
const labels: Record<string, string> = {
  NOT_INSTALLED: "Поиск по файлам ещё не подготовлен",
  CHECKING: "Подготовка поиска по файлам…",
  DOWNLOADING: "Скачивается модель поиска multilingual-e5-small",
  VERIFYING: "Проверка модели…",
  READY: "Поиск по файлам готов",
  FAILED: "Не удалось подготовить поиск по файлам",
  CANCELLED: "Подготовка остановлена",
};
export default function EmbeddingModelStatus({
  api,
  onReady,
}: {
  api: Api;
  onReady?: (ready: boolean) => void;
}) {
  const [model, setModel] = useState<EmbeddingState | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    function update(value: EmbeddingState) {
      if (!controller.signal.aborted) {
        setModel(value);
        onReady?.(value.ready);
      }
    }
    async function listen() {
      while (!controller.signal.aborted) {
        try {
          update(
            await api.json<EmbeddingState>("/rag/model", {
              signal: controller.signal,
            }),
          );
          await api.events("/rag/model/events", controller.signal, (event) => {
            if (event.event === "model")
              update(event.data as unknown as EmbeddingState);
          });
        } catch {
          if (!controller.signal.aborted)
            setError("Не удалось получить состояние модели");
        }
        if (!controller.signal.aborted)
          await new Promise<void>((resolve) => {
            const timer = setTimeout(resolve, 3000);
            controller.signal.addEventListener(
              "abort",
              () => {
                clearTimeout(timer);
                resolve();
              },
              { once: true },
            );
          });
      }
    }
    void listen();
    return () => controller.abort();
  }, [api, onReady]);
  async function act(action: string) {
    setBusy(true);
    setError("");
    try {
      const value = await api.json<EmbeddingState>("/rag/model/" + action, {
        method: "POST",
        signal: AbortSignal.timeout(60000),
      });
      setModel(value);
      onReady?.(value.ready);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка подготовки");
    } finally {
      setBusy(false);
    }
  }
  const active =
    model && ["CHECKING", "DOWNLOADING", "VERIFYING"].includes(model.state);
  return (
    <section aria-label="Embedding model" className="embedding-model">
      <p role="status">
        {model
          ? labels[model.state] || model.state
          : "Проверка состояния поиска по файлам…"}
      </p>
      {model && !model.ready && (
        <>
          <p>
            {(model.downloaded_bytes / 1e6).toFixed(1)} MB
            {model.total_bytes
              ? " / " + (model.total_bytes / 1e6).toFixed(1) + " MB"
              : ""}
          </p>
          {model.state === "DOWNLOADING" && model.total_bytes && (
            <progress
              aria-label="Загрузка модели"
              value={model.downloaded_bytes}
              max={model.total_bytes}
            />
          )}
          {!active && (
            <button
              type="button"
              disabled={busy}
              onClick={() =>
                void act(model.state === "NOT_INSTALLED" ? "prepare" : "retry")
              }
            >
              {model.state === "NOT_INSTALLED"
                ? "Подготовить поиск по файлам"
                : "Повторить подготовку"}
            </button>
          )}
          {active && model.can_cancel && (
            <button
              type="button"
              disabled={busy || model.cancel_requested}
              onClick={() => void act("cancel")}
            >
              {model.cancel_requested
                ? "Остановка загрузки…"
                : "Отменить подготовку"}
            </button>
          )}
        </>
      )}
      {(error || model?.error) && <p role="alert">{error || model?.error}</p>}
    </section>
  );
}
