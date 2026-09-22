import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { ArrowUp, Globe, Paperclip, Square } from "lucide-react";
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
  contextFailed = false,
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
  contextFailed?: boolean;
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
  const showStatus =
    streaming ||
    phase === "stopped" ||
    phase === "error" ||
    phase === "completed";
  const statusText = streaming
    ? `${
        phase === "sending"
          ? "Отправляем запрос…"
          : phase === "streaming"
            ? "Ответ поступает…"
            : elapsed < 8
              ? "Ожидаем первый ответ модели…"
              : "Модель обрабатывает запрос…"
      } ${elapsed} с`
    : phase === "stopped"
      ? "Генерация остановлена"
      : phase === "error"
        ? "Ошибка генерации"
        : phase === "completed"
          ? "Ответ завершён"
          : "";
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
      <ContextUsageMeter usage={contextUsage} failed={contextFailed} />
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
          <label className="composer-select">
            <span className="composer-select-label">Web</span>
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
          <label className="composer-select">
            <span className="composer-select-label">Tor</span>
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
          <label className="composer-select">
            <span className="composer-select-label">Computer</span>
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
          {webMode === "auto" && (
            <button
              type="button"
              className="icon-action"
              aria-label="Найти в интернете"
              title="Найти в интернете"
              disabled={busy || !draft.trim() || !connected}
              onClick={() => void submit("on")}
            >
              <Globe size={15} />
            </button>
          )}
          <button
            type="button"
            className="icon-action"
            aria-label="Прикрепить файл"
            title="Прикрепить файл"
            disabled={busy}
            onClick={() => window.dispatchEvent(new Event("alex-attach"))}
          >
            <Paperclip size={15} />
          </button>
          <span
            className={`${deviceOnline ? "device-chip online" : "device-chip"}`}
            role="status"
            aria-label={`Device: ${deviceLabel} ${deviceOnline ? "Online" : "Offline"}`}
            title={`${deviceLabel} · ${deviceOnline ? "Online" : "Offline"}`}
          >
            <span className="tiny-dot" />
            {deviceOnline ? "PC Online" : "PC Offline"}
          </span>
          {showStatus && <span className="composer-status">{statusText}</span>}
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
