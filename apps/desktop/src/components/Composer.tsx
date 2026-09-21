import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { ArrowUp, Square } from "lucide-react";
import type { ComputerMode, TorMode, WebMode } from "../lib/tools";
import type { ContextUsage } from "../lib/context-usage";
import ContextUsageMeter from "./ContextUsageMeter";

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
  torMode = "auto",
  setTorMode,
  computerMode = "ask",
  setComputerMode,
  deviceLabel = "Not paired",
  deviceOnline = false,
  contextUsage = null,
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
  torMode?: TorMode;
  setTorMode?: (mode: TorMode) => void;
  computerMode?: ComputerMode;
  setComputerMode?: (mode: ComputerMode) => void;
  deviceLabel?: string;
  deviceOnline?: boolean;
  contextUsage?: ContextUsage | null;
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
      <ContextUsageMeter usage={contextUsage} />
      <div className="composer">
        <textarea
          ref={textarea}
          aria-label="Сообщение"
          placeholder="Напишите сообщение Canalla…"
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
          {webMode === "auto" && (
            <button
              type="button"
              disabled={busy || !draft.trim() || !connected}
              onClick={() => void submit("on")}
            >
              Найти в интернете
            </button>
          )}
          <label>
            Tor{" "}
            <select
              aria-label="Tor mode"
              disabled={busy}
              value={torMode}
              onChange={(e) => setTorMode?.(e.target.value as TorMode)}
            >
              <option value="off">Off</option>
              <option value="auto">Auto</option>
              <option value="on">On</option>
            </select>
          </label>
          <label>
            Computer{" "}
            <select
              aria-label="Computer mode"
              disabled={busy}
              value={computerMode}
              onChange={(e) =>
                setComputerMode?.(e.target.value as ComputerMode)
              }
            >
              <option value="off">Off</option>
              <option value="ask">Ask</option>
              <option value="trusted">Trusted Workspace</option>
            </select>
          </label>
          <span
            className={`connection ${deviceOnline ? "connected" : ""}`}
            role="status"
            aria-label="Device status"
          >
            <span className="tiny-dot" /> Device: {deviceLabel}{" "}
            {deviceOnline ? "Online" : "Offline"}
          </span>
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
