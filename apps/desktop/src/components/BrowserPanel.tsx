import { useEffect, useRef, useState } from "react";
import type { Api } from "../lib/api";
import { toolErrors, type ToolRun } from "../lib/tools";
import ToolActivity from "./ToolActivity";

export default function BrowserPanel({
  api,
  chatId,
  onClose,
}: {
  api: Api;
  chatId: string | null;
  onClose: () => void;
}) {
  const [url, setUrl] = useState("https://example.com");
  const [session, setSession] = useState<string | null>(null);
  const sessionRef = useRef<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  const [action, setAction] = useState("read");
  const [selector, setSelector] = useState("body");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [runs, setRuns] = useState<ToolRun[]>([]);
  const [reply, setReply] = useState("");
  const [image, setImage] = useState("");
  const [error, setError] = useState("");
  useEffect(
    () => () => {
      controller.current?.abort();
      if (sessionRef.current)
        void api
          .json(
            "/tools/browser/" +
              encodeURIComponent(sessionRef.current) +
              "/stop",
            { method: "POST" },
          )
          .catch(() => {});
    },
    [api],
  );
  async function execute(start = false) {
    if (!chatId) return;
    setBusy(true);
    setError("");
    setReply("");
    const abort = new AbortController();
    controller.current = abort;
    const name = start
      ? "browser_start"
      : ["click", "type"].includes(action)
        ? "browser_write"
        : "browser_read";
    const args = start
      ? { url }
      : name === "browser_write"
        ? { session_id: session, action, selector, text }
        : {
            session_id: session,
            action,
            selector,
            ...(action === "navigate" ? { url } : {}),
          };
    try {
      await api.events(
        "/tools/execute",
        abort.signal,
        (event) => {
          if (event.event === "tool") {
            const run = event.data as unknown as ToolRun;
            setRuns((previous) => [
              ...previous.filter((row) => row.id !== run.id),
              run,
            ]);
          }
          if (event.event === "tool_error")
            setError(
              toolErrors[String(event.data.code)] ||
                "Операция браузера не выполнена",
            );
          if (event.event === "tool_result") {
            setReply(String(event.data.text || ""));
            const metadata = event.data.metadata as Record<string, unknown>;
            if (start && typeof metadata?.session_id === "string") {
              setSession(metadata.session_id);
              sessionRef.current = metadata.session_id;
            }
          }
        },
        {
          method: "POST",
          body: JSON.stringify({ name, arguments: args, chat_id: chatId }),
        },
      );
      if (action === "screenshot" && session)
        setImage(
          await api.imageData(
            "/tools/browser/" + encodeURIComponent(session) + "/screenshot",
          ),
        );
    } catch (e) {
      if (!abort.signal.aborted)
        setError(e instanceof Error ? e.message : "Ошибка браузера");
    } finally {
      setBusy(false);
      controller.current = null;
    }
  }
  async function stop(close = false) {
    controller.current?.abort();
    if (sessionRef.current) {
      try {
        const result = await api.json<{ supplier_stop_confirmed: boolean }>(
          "/tools/browser/" + encodeURIComponent(sessionRef.current) + "/stop",
          { method: "POST", signal: AbortSignal.timeout(60000) },
        );
        if (!result.supplier_stop_confirmed) {
          setError(
            "Локальные команды остановлены; завершение у TinyFish не подтверждено.",
          );
          return;
        }
        sessionRef.current = null;
        setSession(null);
        setReply("Browser остановлен у провайдера.");
      } catch (e) {
        setError(
          e instanceof Error ? e.message : "Не удалось подтвердить остановку",
        );
        return;
      }
    }
    if (close) onClose();
  }
  return (
    <dialog open className="settings-dialog" aria-label="Browser Advanced">
      <h2>Browser Advanced · Paid</h2>
      <p>
        Только явные команды. Пароли и платёжные данные не поддерживаются.
        Сессия ограничена бюджетом и таймером backend.
      </p>
      {!chatId && <p>Сначала выберите существующий диалог.</p>}
      <label>
        Публичный URL
        <input
          aria-label="Browser URL"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
        />
      </label>
      {!session ? (
        <button
          type="button"
          disabled={busy || !chatId}
          onClick={() => void execute(true)}
        >
          Создать платную Browser-сессию
        </button>
      ) : (
        <>
          <p>Session: {session}</p>
          <label>
            Действие
            <select
              aria-label="Browser action"
              value={action}
              onChange={(e) => setAction(e.target.value)}
            >
              {[
                "navigate",
                "read",
                "wait",
                "screenshot",
                "extract",
                "click",
                "type",
              ].map((value) => (
                <option key={value}>{value}</option>
              ))}
            </select>
          </label>
          <label>
            Selector
            <input
              aria-label="Browser selector"
              value={selector}
              onChange={(e) => setSelector(e.target.value)}
            />
          </label>
          {action === "type" && (
            <label>
              Текст без секретов
              <input
                aria-label="Browser text"
                value={text}
                maxLength={1000}
                onChange={(e) => setText(e.target.value)}
              />
            </label>
          )}
          <button type="button" disabled={busy} onClick={() => void execute()}>
            Выполнить команду
          </button>
        </>
      )}
      <button type="button" onClick={() => void stop()}>
        Stop Browser
      </button>
      <button type="button" onClick={() => void stop(true)}>
        Закрыть Browser
      </button>
      <ToolActivity
        api={api}
        runs={runs}
        state={busy ? "browser_working" : ""}
      />
      {reply && <pre>{reply}</pre>}
      {image && (
        <img src={image} alt="Снимок Browser" style={{ maxWidth: "100%" }} />
      )}
      {error && <p role="alert">{error}</p>}
    </dialog>
  );
}
