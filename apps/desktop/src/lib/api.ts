import type { Chat, Health, Message, User } from "../types";
import { consumeSSE, type ServerEvent } from "./sse";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export function validateBackendUrl(value: string): string {
  const url = new URL(value.trim());
  const local = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
  if (url.protocol !== "https:" && !(local && url.protocol === "http:")) {
    throw new Error(
      "Для удалённого backend нужен HTTPS. HTTP разрешён только для localhost.",
    );
  }
  if (url.username || url.password || url.search || url.hash)
    throw new Error("Укажите URL без пароля, query и fragment.");
  return url.toString().replace(/\/$/, "");
}

export class Api {
  constructor(
    public base: string,
    private token: string | null,
    private refresh?: () => Promise<string | null>,
  ) {}
  authToken() {
    return this.token;
  }
  setToken(token: string | null) {
    this.token = token;
  }
  /** Single-flight refresh: parallel 401s share one refresh attempt. */
  private refreshPromise: Promise<string | null> | null = null;
  private refreshOnce(): Promise<string | null> {
    if (!this.refresh) return Promise.resolve(null);
    if (!this.refreshPromise) {
      this.refreshPromise = Promise.resolve()
        .then(() => this.refresh!())
        .catch(() => null)
        .finally(() => {
          this.refreshPromise = null;
        });
    }
    return this.refreshPromise;
  }
  private async response(path: string, init: RequestInit = {}) {
    const attempt = async () => {
      const headers = new Headers(init.headers);
      if (init.body && !(init.body instanceof FormData))
        headers.set("Content-Type", "application/json");
      if (this.token) headers.set("Authorization", `Bearer ${this.token}`);
      return fetch(this.base + path, {
        ...init,
        headers,
        signal: init.signal ?? AbortSignal.timeout(15000),
        credentials: "omit",
        redirect: "error",
      });
    };
    let response = await attempt();
    if (response.status === 401 && !path.startsWith("/auth/") && this.refresh) {
      // One refresh attempt, then one safe retry. The original request was
      // rejected at authentication, so retrying cannot double-apply it.
      const fresh = await this.refreshOnce();
      if (fresh) {
        this.token = fresh;
        response = await attempt();
      }
    }
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new ApiError(
        response.status,
        typeof body.detail === "string"
          ? body.detail
          : `Ошибка запроса (${response.status})`,
      );
    }
    return response;
  }
  async json<T>(path: string, init?: RequestInit): Promise<T> {
    return (await this.response(path, init)).json();
  }
  async events(
    path: string,
    signal: AbortSignal,
    onEvent: (event: ServerEvent) => void,
    init: RequestInit = {},
  ) {
    const response = await this.response(path, { ...init, signal });
    if (!response.body) throw new Error("Поток событий недоступен");
    await consumeSSE(response.body, onEvent);
  }
  async imageData(path: string): Promise<string> {
    const blob = await (await this.response(path)).blob();
    return await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = () => reject(new Error("Не удалось прочитать снимок"));
      reader.readAsDataURL(blob);
    });
  }
  auth(mode: "login" | "register", email: string, password: string) {
    return this.json<{ access_token: string }>(`/auth/${mode}`, {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
  }
  me() {
    return this.json<User>("/auth/me");
  }
  health() {
    return this.json<Health>("/health");
  }
  private async all<T>(path: string, limit: number): Promise<T[]> {
    const result: T[] = [];
    for (let offset = 0; ; offset += limit) {
      const page = await this.json<T[]>(
        `${path}?offset=${offset}&limit=${limit}`,
      );
      result.push(...page);
      if (page.length < limit) return result;
    }
  }
  chats() {
    return this.all<Chat>("/chats", 100);
  }
  messages(id: string) {
    return this.all<Message>(`/chats/${id}/messages`, 200);
  }
  async settledMessages(id: string): Promise<Message[]> {
    for (let attempt = 0; attempt < 30; attempt++) {
      const state = await this.json<{ active: boolean }>(
        `/chats/${id}/generation`,
      );
      if (!state.active) return this.messages(id);
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    throw new Error(
      "Сервер ещё завершает ответ. Откройте диалог повторно через несколько секунд.",
    );
  }
  createChat() {
    return this.json<Chat>("/chats", { method: "POST", body: "{}" });
  }
  async deleteChat(id: string) {
    await this.response(`/chats/${id}`, { method: "DELETE" });
  }
  updateChat(
    id: string,
    patch: Partial<Pick<Chat, "title" | "pinned" | "project_id">>,
  ) {
    return this.json<Chat>(`/chats/${id}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    });
  }
  editMessage(chat: string, id: string, content: string) {
    return this.json<Message>(`/chats/${chat}/messages/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ content }),
    });
  }
  async exportChats(format: "json" | "markdown", id?: string) {
    return (
      await this.response(`/chats${id ? `/${id}` : ""}/export?format=${format}`)
    ).text();
  }
  async stream(
    id: string,
    content: string,
    signal: AbortSignal,
    onEvent: (event: ServerEvent) => void,
    action?: { kind: "resend" | "regenerate"; messageId: string },
    webMode?: "off" | "auto" | "on",
    computerMode?: "off" | "ask" | "trusted",
    torMode?: "off" | "auto" | "on",
    resumeTaskId?: string,
  ) {
    const path = resumeTaskId
      ? `/tasks/${resumeTaskId}/resume`
      : action
        ? `/chats/${id}/messages/${action.messageId}/${action.kind}`
        : `/chats/${id}/stream`;
    const response = await this.response(path, {
      method: "POST",
      body:
        action?.kind === "regenerate" || resumeTaskId
          ? undefined
          : JSON.stringify({
              content,
              web_mode: webMode,
              computer_mode: computerMode,
              tor_mode: torMode,
            }),
      signal,
    });
    if (!response.body) throw new Error("Streaming не поддерживается");
    let finished = false;
    await consumeSSE(response.body, (event) => {
      if (event.event === "done") finished = true;
      if (event.event === "error") throw new Error(String(event.data.detail));
      onEvent(event);
    });
    if (!finished && !signal.aborted)
      throw new Error("Соединение прервано. Частичный ответ сохранён.");
  }
}
