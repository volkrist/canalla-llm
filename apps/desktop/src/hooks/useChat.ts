import { useCallback, useEffect, useRef, useState } from "react";
import { Api, ApiError } from "../lib/api";
import type { Chat, Message } from "../types";

export function useChat(api: Api, onExpired: () => void) {
  const [chats, setChats] = useState<Chat[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [busy, setBusy] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState("");
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

  async function send(content: string): Promise<boolean> {
    if (locked.current) return false;
    locked.current = true;
    setBusy(true);
    setError("");
    const abort = new AbortController();
    controller.current = abort;
    let accepted = false;
    let id = selected;
    try {
      if (!id) {
        const chat = await api.createChat();
        if (!active.current || abort.signal.aborted) return false;
        id = chat.id;
        setSelected(id);
        setChats((prev) => [chat, ...prev]);
      }
      setStreaming(true);
      await api.stream(id, content, abort.signal, (event) => {
        if (!active.current) return;
        if (event.event === "meta") {
          accepted = true;
          const user = event.data.user as unknown as Message;
          const assistant = event.data.assistant as unknown as Message;
          setMessages((prev) => [...prev, user, assistant]);
        }
        if (event.event === "delta") {
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
      });
    } catch (e) {
      if (!abort.signal.aborted) handleError(e);
    } finally {
      controller.current = null;
      if (active.current && accepted && id) {
        try {
          const saved = await api.settledMessages(id);
          if (active.current) setMessages(saved);
        } catch (e) {
          handleError(e);
        }
      }
      if (active.current) {
        setStreaming(false);
        setBusy(false);
        setMessages((prev) => prev.filter((message) => message.content !== ""));
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
    error,
    select,
    remove,
    send,
    stop: () => controller.current?.abort(),
    clearError: () => setError(""),
  };
}
