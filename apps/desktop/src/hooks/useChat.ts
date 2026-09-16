import { useCallback, useEffect, useRef, useState } from "react";
import { Api, ApiError } from "../lib/api";
import type { Chat, Message } from "../types";
import type { ToolRun, WebMode } from "../lib/tools";

export function useChat(api: Api, onExpired: () => void) {
  const [chats, setChats] = useState<Chat[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [busy, setBusy] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [phase, setPhase] = useState("idle");
  const [error, setError] = useState("");
  const [webMode, setWebMode] = useState<WebMode>("auto");
  const [webState, setWebState] = useState("");
  const [webError, setWebError] = useState("");
  const [toolRuns, setToolRuns] = useState<ToolRun[]>([]);
  useEffect(() => {
    let live = true;
    const refresh = () =>
      void api
        .json<{ default_mode: WebMode }>("/tools/preferences")
        .then((value) => {
          if (live) setWebMode(value.default_mode);
        })
        .catch(() => {});
    refresh();
    window.addEventListener("alex-tools-settings", refresh);
    return () => {
      live = false;
      window.removeEventListener("alex-tools-settings", refresh);
    };
  }, [api]);
  const controller = useRef<AbortController | null>(null);
  const active = useRef(true);
  const locked = useRef(false);
  const handleError = useCallback(
    (e: unknown) => {
      if (!active.current) return;
      if (e instanceof ApiError && e.status === 401) {
        onExpired();
        return;
      }
      setError(e instanceof Error ? e.message : "Не удалось выполнить запрос");
    },
    [onExpired],
  );

  useEffect(() => {
    active.current = true;
    let cancelled = false;
    api
      .chats()
      .then((data) => {
        if (!cancelled) setChats(data);
      })
      .catch((e) => {
        if (!cancelled) handleError(e);
      });
    return () => {
      cancelled = true;
      active.current = false;
      controller.current?.abort();
    };
  }, [api, handleError]);

  async function select(id: string | null) {
    if (locked.current) return;
    locked.current = true;
    setBusy(true);
    setError("");
    try {
      const data = id ? await api.messages(id) : [];
      if (active.current) {
        setSelected(id);
        setMessages(data);
        setWebState("");
        setWebError("");
        setToolRuns(
          id
            ? await api.json<ToolRun[]>(
                "/tools/runs?chat_id=" + encodeURIComponent(id),
              )
            : [],
        );
      }
    } catch (e) {
      handleError(e);
    } finally {
      locked.current = false;
      if (active.current) setBusy(false);
    }
  }

  async function remove(id: string) {
    if (locked.current) return;
    locked.current = true;
    setBusy(true);
    setError("");
    try {
      await api.deleteChat(id);
      setChats((prev) => prev.filter((chat) => chat.id !== id));
      if (selected === id) {
        setSelected(null);
        setMessages([]);
      }
    } catch (e) {
      handleError(e);
    } finally {
      locked.current = false;
      if (active.current) setBusy(false);
    }
  }

  async function send(
    content: string,
    action?: { kind: "resend" | "regenerate"; messageId: string },
    mode?: WebMode,
  ): Promise<boolean> {
    if (locked.current) return false;
    locked.current = true;
    setBusy(true);
    setError("");
    setWebError("");
    setWebState("");
    setToolRuns([]);
    setPhase("sending");
    setStreaming(true);
    const abort = new AbortController();
    controller.current = abort;
    let accepted = false;
    let id = selected;
    try {
      if (!id) {
        const chat = await api.createChat();
        if (!active.current || abort.signal.aborted) {
          if (active.current) setPhase("stopped");
          return false;
        }
        id = chat.id;
        setSelected(id);
        setChats((prev) => [chat, ...prev]);
      }
      setStreaming(true);
      await api.stream(
        id,
        content,
        abort.signal,
        (event) => {
          if (!active.current) return;
          if (event.event === "web_status") {
            setWebState(String(event.data.state));
            if (event.data.code) setWebError(String(event.data.code));
          }
          if (event.event === "tool") {
            const run = event.data as unknown as ToolRun;
            setToolRuns((previous) => [
              ...previous.filter((item) => item.id !== run.id),
              run,
            ]);
            setWebState(run.status);
            if (run.status === "completed")
              window.dispatchEvent(new Event("alex-web-sources"));
          }
          if (event.event === "meta") {
            accepted = true;
            setPhase("waiting");
            const user = event.data.user as unknown as Message;
            const assistant = event.data.assistant as unknown as Message;
            setMessages((prev) => {
              const replaceId = event.data.replace_after_id;
              const index = replaceId
                ? prev.findIndex((message) => message.id === replaceId)
                : -1;
              return [
                ...(index >= 0 ? prev.slice(0, index) : prev),
                user,
                assistant,
              ];
            });
          }
          if (event.event === "delta") {
            setPhase("streaming");
            setMessages((prev) =>
              prev.map((message, i) =>
                i === prev.length - 1
                  ? {
                      ...message,
                      content: message.content + String(event.data.content),
                    }
                  : message,
              ),
            );
          }
        },
        action,
        mode ?? webMode,
      );
      setPhase("completed");
      setWebState("completed");
    } catch (e) {
      setPhase(abort.signal.aborted ? "stopped" : "error");
      setWebState(abort.signal.aborted ? "stopped" : "failed");
      if (!abort.signal.aborted) handleError(e);
    } finally {
      controller.current = null;
      if (active.current && accepted && id) {
        try {
          const saved = await api.settledMessages(id);
          if (active.current) setMessages(saved);
          const runs = await api.json<ToolRun[]>(
            "/tools/runs?chat_id=" + encodeURIComponent(id),
          );
          if (active.current) setToolRuns(runs);
        } catch (e) {
          handleError(e);
        }
      }
      if (active.current) {
        setStreaming(false);
        setBusy(false);
        api
          .chats()
          .then((data) => {
            if (active.current) setChats(data);
          })
          .catch(handleError);
      }
      locked.current = false;
    }
    return accepted;
  }

  return {
    chats,
    selected,
    messages,
    busy,
    streaming,
    phase,
    webMode,
    setWebMode,
    webState,
    webError,
    toolRuns,
    error,
    select,
    remove,
    send,
    update: async (
      id: string,
      patch: Partial<Pick<Chat, "title" | "pinned" | "project_id">>,
    ) => {
      try {
        await api.updateChat(id, patch);
        setChats(await api.chats());
      } catch (e) {
        handleError(e);
      }
    },
    edit: async (id: string, content: string) => {
      if (!selected || locked.current) return;
      try {
        const saved = await api.editMessage(selected, id, content);
        setMessages((prev) =>
          prev.map((message) => (message.id === id ? saved : message)),
        );
      } catch (e) {
        handleError(e);
      }
    },
    stop: () => controller.current?.abort(),
    clearError: () => setError(""),
  };
}
