import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { ArrowUp, Square } from "lucide-react";
import type { WebMode } from "../lib/tools";

export default function Composer({
  busy,
  streaming,
  phase = "idle",
  connected,
  onSend,
  onStop,
  draft,
  setDraft,
  enterSends = true,
  webMode = "auto",
  setWebMode,
}: {
  busy: boolean;
  streaming: boolean;
  phase?: string;
  connected: boolean;
  onSend: (text: string, mode?: WebMode) => Promise<boolean>;
  onStop: () => void;
  draft: string;
  setDraft: (text: string) => void;
  enterSends?: boolean;
  webMode?: WebMode;
  setWebMode?: (mode: WebMode) => void;
}) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!streaming) return;
    const start = Date.now();
    setElapsed(0);
    const timer = setInterval(
      () => setElapsed(Math.floor((Date.now() - start) / 1000)),
      1000,
    );
    return () => clearInterval(timer);
  }, [streaming]);
  const textarea = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const element = textarea.current;
    if (element) {
      element.style.height = "auto";
      element.style.height = `${Math.min(element.scrollHeight, 220)}px`;
    }
  }, [draft]);
  const [sending, setSending] = useState(false);
  async function submit(mode?: WebMode) {
    const text = draft.trim();
    if (!text || busy || sending || !connected) return;
    setSending(true);
    setDraft("");
    const accepted = await onSend(text, mode ?? webMode);
    if (!accepted) setDraft(text);
    setSending(false);
  }
  function keyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (
      event.key === "Enter" &&
      !event.shiftKey &&
      (enterSends || event.ctrlKey || event.metaKey) &&
      !event.nativeEvent.isComposing
    ) {
      event.preventDefault();
      void submit();
    }
  }
  return (
    <div className="composer-wrap">
      <div className="composer">
        <textarea
          ref={textarea}
          aria-label="Сообщение"
          placeholder="Напишите сообщение Alex…"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={keyDown}
          maxLength={32000}
          disabled={busy}
          rows={2}
        />
        <div className="composer-toolbar">
          <label>
            Web{" "}
            <select
              aria-label="Web mode"
              disabled={busy}
              value={webMode}
              onChange={(e) => setWebMode?.(e.target.value as WebMode)}
            >
              <option value="off">Off</option>
              <option value="auto">Auto</option>
              <option value="on">On</option>
            </select>
          </label>
          <button
            type="button"
            disabled={busy || !draft.trim() || !connected}
            onClick={() => void submit("on")}
          >
            Найти в интернете
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => window.dispatchEvent(new Event("alex-attach"))}
          >
            Прикрепить файл
          </button>
          <span>
            <span className="tiny-dot" />{" "}
            {streaming
              ? `${phase === "sending" ? "Отправляем запрос…" : phase === "streaming" ? "Ответ поступает…" : elapsed < 8 ? "Ожидаем первый ответ модели…" : "Модель обрабатывает запрос…"} ${elapsed} с`
              : phase === "stopped"
                ? "Генерация остановлена"
                : phase === "error"
                  ? "Ошибка генерации"
                  : phase === "completed"
                    ? "Ответ завершён"
                    : "Ваш следующий вопрос"}
          </span>
          {streaming ? (
            <button
              className="send-button stop"
              aria-label="Stop generation"
              onClick={onStop}
            >
              <Square size={15} fill="currentColor" />
            </button>
          ) : (
            <button
              className="send-button"
              aria-label="Send"
              disabled={busy || sending || !draft.trim() || !connected}
              onClick={() => void submit()}
            >
              <ArrowUp size={21} />
            </button>
          )}
        </div>
      </div>
      <p className="composer-hint">
        {enterSends ? "Enter" : "Ctrl + Enter"} — отправить <span>·</span> Shift
        + Enter — новая строка
      </p>
    </div>
  );
}
