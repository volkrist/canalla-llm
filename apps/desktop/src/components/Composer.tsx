import { useState, type KeyboardEvent } from "react";
import { ArrowUp, Square } from "lucide-react";

export default function Composer({
  busy,
  streaming,
  connected,
  onSend,
  onStop,
  draft,
  setDraft,
}: {
  busy: boolean;
  streaming: boolean;
  connected: boolean;
  onSend: (text: string) => Promise<boolean>;
  onStop: () => void;
  draft: string;
  setDraft: (text: string) => void;
}) {
  const [sending, setSending] = useState(false);
  async function submit() {
    const text = draft.trim();
    if (!text || busy || sending || !connected) return;
    setSending(true);
    setDraft("");
    const accepted = await onSend(text);
    if (!accepted) setDraft(text);
    setSending(false);
  }
  function keyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (
      event.key === "Enter" &&
      !event.shiftKey &&
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
          <span>
            <span className="tiny-dot" />{" "}
            {streaming ? "Alex отвечает…" : "Ваш следующий вопрос"}
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
        Enter — отправить <span>·</span> Shift + Enter — новая строка
      </p>
    </div>
  );
}
