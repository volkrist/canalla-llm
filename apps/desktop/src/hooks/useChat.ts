import { useCallback, useEffect, useRef, useState } from "react";
import { Api, ApiError } from "../lib/api";
import type { Chat, Message } from "../types";
import type {
  ToolRun,
  WebMode,
  ComputerMode,
  TorMode,
  AutonomousTask,
} from "../lib/tools";
import { publicAssistantText } from "../lib/tools";

type SendAction = { kind: "resend" | "regenerate"; messageId: string };
type PendingSend = {
  content: string;
  action?: SendAction;
  mode?: WebMode;
  resumeTaskId?: string;
};
type StreamState = {
  chatId: string;
  controller: AbortController;
  content: string;
  assistantId: string | null;
  assistant: Message | null;
  runs: ToolRun[];
};

export function useChat(api: Api, onExpired: () => void) {
  const [chats, setChats] = useState<Chat[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [queued, setQueued] = useState(false);
  const [phase, setPhase] = useState("idle");
  const [error, setError] = useState("");
  const [webMode, setWebMode] = useState<WebMode>("auto");
  const [torMode, setTorMode] = useState<TorMode>("auto");
  const [computerMode, setComputerMode] = useState<ComputerMode>("ask");
  const [webState, setWebState] = useState("");
  const [webError, setWebError] = useState("");
  const [toolRuns, setToolRuns] = useState<ToolRun[]>([]);
  const [task, setTask] = useState<AutonomousTask | null>(null);
  const [tasks, setTasks] = useState<AutonomousTask[]>([]);
  useEffect(() => {
    let live = true;
    const refresh = () =>
      void api
        .json<{
          default_mode: WebMode;
          computer_mode: ComputerMode;
          tor_mode: TorMode;
        }>("/tools/preferences")
        .then((value) => {
          if (live) {
            setWebMode(value.default_mode);
            if (value.computer_mode) setComputerMode(value.computer_mode);
            if (value.tor_mode) setTorMode(value.tor_mode);
          }
        })
        .catch(() => {});
    refresh();
    window.addEventListener("alex-tools-settings", refresh);
    return () => {
      live = false;
      window.removeEventListener("alex-tools-settings", refresh);
    };
  }, [api]);
  const active = useRef(true);
  const selecting = useRef(false);
  const selectedRef = useRef<string | null>(null);
  const streamRef = useRef<StreamState | null>(null);
  const pendingRef = useRef<PendingSend | null>(null);
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
    selectedRef.current = selected;
  }, [selected]);

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
      streamRef.current?.controller.abort();
    };
  }, [api, handleError]);

  async function select(id: string | null) {
    // Navigation is never blocked by a running generation. The stream keeps writing to its own
    // chat; only the visible message list follows the selected chat.
    if (selecting.current) return;
    selecting.current = true;
    setLoading(true);
    setError("");
    try {
      let data = id ? await api.messages(id) : [];
      if (!active.current) return;
      // Coming back to the chat that is still streaming: the database has the messages created
      // at `meta`, but the live response body lives in the stream cache.
      const stream = streamRef.current;
      if (id && stream && stream.chatId === id && stream.assistantId) {
        data = data.map((message) =>
          message.id === stream.assistantId
            ? { ...message, content: stream.content }
            : message,
        );
        if (!data.some((message) => message.id === stream.assistantId) && stream.assistant) {
          data = [
            ...data,
            { ...stream.assistant, content: stream.content },
          ];
        }
      }
      selectedRef.current = id;
      setSelected(id);
      setMessages(data);
      setWebState("");
      setWebError("");
      const runs = id
        ? await api.json<ToolRun[]>(
            "/tools/runs?chat_id=" + encodeURIComponent(id),
          )
        : [];
      const live = stream && id === stream.chatId;
      if (active.current) {
        setToolRuns(
          live
            ? runs.map(
                (run) => stream!.runs.find((item) => item.id === run.id) || run,
              )
            : runs,
        );
      }
      setTask(null);
      if (id) {
        const listed = await api.json<AutonomousTask[]>(
          "/tasks?chat_id=" + encodeURIComponent(id),
        );
        const current =
          listed.find(
            (item) => !["COMPLETED", "FAILED", "STOPPED"].includes(item.status),
          ) || listed[0];
        if (current) setTask(current);
      }
      const history = await api.json<AutonomousTask[]>("/tasks");
      if (active.current) setTasks(history);
    } catch (e) {
      handleError(e);
    } finally {
      selecting.current = false;
      if (active.current) setLoading(false);
    }
  }

  async function remove(id: string) {
    setError("");
    try {
      await api.deleteChat(id);
      setChats((prev) => prev.filter((chat) => chat.id !== id));
      if (selectedRef.current === id) {
        if (streamRef.current?.chatId === id) {
          streamRef.current.controller.abort();
        }
        selectedRef.current = null;
        setSelected(null);
        setMessages([]);
      }
    } catch (e) {
      handleError(e);
    }
  }

  async function send(
    content: string,
    action?: SendAction,
    mode?: WebMode,
    resumeTaskId?: string,
  ): Promise<boolean> {
    // A second message during generation is queued, never a concurrent request to the same
    // llama.cpp session. The composer stays usable while the first answer streams.
    if (streamRef.current) {
      pendingRef.current = { content, action, mode, resumeTaskId };
      setQueued(true);
      return true;
    }
    setError("");
    setWebError("");
    setWebState("");
    setToolRuns([]);
    setPhase("sending");
    setStreaming(true);
    const abort = new AbortController();
    let accepted = false;
    let id = selectedRef.current;
    const state: StreamState = {
      chatId: id || "",
      controller: abort,
      content: "",
      assistantId: null,
      assistant: null,
      runs: [],
    };
    streamRef.current = state;
    try {
      if (!id) {
        const chat = await api.createChat();
        if (!active.current || abort.signal.aborted) {
          if (active.current) setPhase("stopped");
          return false;
        }
        id = chat.id;
        state.chatId = id;
        selectedRef.current = id;
        setSelected(id);
        setChats((prev) => [chat, ...prev]);
      }
      await api.stream(
        id,
        content,
        abort.signal,
        (event) => {
          if (!active.current) return;
          const visible = () => selectedRef.current === id;
          if (event.event === "progress") {
            setPhase(String(event.data.state || "waiting"));
            if (visible() && event.data.text) {
              setWebState(String(event.data.text));
            }
          }
          if (event.event === "web_status") {
            if (!visible()) return;
            setWebState(String(event.data.state));
            if (event.data.code) setWebError(String(event.data.code));
            else if (
              ["planning", "searching", "reading", "finishing"].includes(
                String(event.data.state),
              )
            ) {
              // A new round (or a clean finish) supersedes the previous per-step failure.
              setWebError("");
            }
          }
          if (event.event === "task" || event.event === "task_status") {
            if (!visible()) return;
            const payload = event.data as unknown as AutonomousTask;
            if (payload.id) setTask(payload);
            else
              setTask((current) =>
                current
                  ? {
                      ...current,
                      status: String(event.data.state || current.status),
                      message: String(
                        event.data.code || current.message || current.status,
                      ),
                    }
                  : current,
              );
          }
          if (event.event === "tool") {
            const run = event.data as unknown as ToolRun;
            state.runs = [
              ...state.runs.filter((item) => item.id !== run.id),
              run,
            ];
            if (!visible()) return;
            setToolRuns((previous) => [
              ...previous.filter((item) => item.id !== run.id),
              run,
            ]);
            setWebState(run.status);
            if (run.status === "completed") {
              // A later success clears the banner a failed step put up earlier in this turn.
              if (
                run.tool_name.startsWith("web_") ||
                run.tool_name.startsWith("tor_")
              ) {
                setWebError("");
              }
              window.dispatchEvent(new Event("alex-web-sources"));
            }
            if (run.status === "waiting_host")
              window.dispatchEvent(new Event("alex-host-jobs"));
          }
          if (event.event === "meta") {
            accepted = true;
            setPhase("waiting");
            const user = event.data.user as unknown as Message;
            const assistant = event.data.assistant as unknown as Message;
            state.assistantId = assistant.id;
            state.assistant = assistant;
            if (visible()) {
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
          }
          if (event.event === "delta") {
            const delta = String(event.data.content);
            state.content += delta;
            setPhase("streaming");
            if (visible() && state.assistantId) {
              setMessages((prev) =>
                prev.map((message) =>
                  message.id === state.assistantId
                    ? {
                        ...message,
                        content: publicAssistantText(message.content + delta),
                      }
                    : message,
                ),
              );
            }
          }
        },
        action,
        mode ?? webMode,
        computerMode,
        torMode,
        resumeTaskId,
      );
      setPhase("completed");
      setWebState("completed");
    } catch (e) {
      setPhase(abort.signal.aborted ? "stopped" : "error");
      setWebState(abort.signal.aborted ? "stopped" : "failed");
      if (!abort.signal.aborted) handleError(e);
    } finally {
      streamRef.current = null;
      if (active.current && accepted && id) {
        try {
          if (selectedRef.current === id) {
            const saved = await api.settledMessages(id);
            if (active.current) setMessages(saved);
            const runs = await api.json<ToolRun[]>(
              "/tools/runs?chat_id=" + encodeURIComponent(id),
            );
            if (active.current) setToolRuns(runs);
          }
        } catch (e) {
          handleError(e);
        }
      }
      if (active.current) {
        setStreaming(false);
        api
          .chats()
          .then((data) => {
            if (active.current) setChats(data);
          })
          .catch(handleError);
      }
      const next = pendingRef.current;
      pendingRef.current = null;
      setQueued(false);
      if (next && active.current) {
        // One turn at a time; the queued message starts after the previous stream settled.
        window.setTimeout(
          () =>
            void send(next.content, next.action, next.mode, next.resumeTaskId),
          0,
        );
      }
    }
    return accepted;
  }

  useEffect(() => {
    if (!task || task.status !== "WAITING_WORKSPACE" || !selected) return;
    const timer = window.setInterval(() => {
      void api
        .json<AutonomousTask[]>(
          "/tasks?chat_id=" + encodeURIComponent(selected),
        )
        .then((listed) => {
          const current = listed.find((item) => item.id === task.id);
          if (!current || !active.current) return;
          setTask(current);
          if (
            (current.status === "READY" || current.status === "RECOVERING") &&
            current.promoted_from_queue &&
            !streamRef.current
          ) {
            void send("", undefined, webMode, current.id);
          }
        })
        .catch(() => {});
    }, 2000);
    return () => window.clearInterval(timer);
  }, [api, selected, task, webMode]);

  return {
    chats,
    selected,
    messages,
    /** True while a chat's messages are being read. Never blocks navigation for long. */
    loading,
    /** True for the whole generation, including tool calls. */
    streaming,
    /** True when a second message waits for the current generation to finish. */
    queued,
    /** Aggregate state kept for callers that want a single "work in progress" flag. */
    busy: loading || streaming,
    phase,
    webMode,
    setWebMode,
    torMode,
    setTorMode,
    computerMode,
    setComputerMode,
    webState,
    webError,
    toolRuns,
    task,
    tasks,
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
      if (!selectedRef.current) return;
      try {
        const saved = await api.editMessage(selectedRef.current, id, content);
        setMessages((prev) =>
          prev.map((message) => (message.id === id ? saved : message)),
        );
      } catch (e) {
        handleError(e);
      }
    },
    stop: () => streamRef.current?.controller.abort(),
    pauseTask: async () => {
      if (!task) return;
      try {
        const next = await api.json<AutonomousTask>(`/tasks/${task.id}/pause`, {
          method: "POST",
        });
        setTask(next);
      } catch (e) {
        handleError(e);
      }
    },
    stopTask: async () => {
      if (!task) return;
      try {
        const next = await api.json<AutonomousTask>(`/tasks/${task.id}/stop`, {
          method: "POST",
        });
        setTask(next);
        streamRef.current?.controller.abort();
      } catch (e) {
        handleError(e);
      }
    },
    resumeTask: async () => {
      if (!task || !selected) return;
      await send("", undefined, webMode, task.id);
    },
    clearError: () => setError(""),
  };
}
