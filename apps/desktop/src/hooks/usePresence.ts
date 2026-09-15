import { useEffect, useState } from "react";
import { Api, ApiError } from "../lib/api";
export interface OnlineUser {
  key: string;
  display_name: string;
  status: "online" | "offline" | "idle";
  using_ai: boolean;
  last_seen: string | null;
}
export function lastSeen(value: string | null, at = Date.now()) {
  if (!value) return "Ещё не был в сети";
  const minutes = Math.max(0, Math.floor((at - Date.parse(value)) / 60000));
  return minutes < 1
    ? "только что"
    : minutes < 60
      ? `${minutes} мин назад`
      : minutes < 1440
        ? `${Math.floor(minutes / 60)} ч назад`
        : minutes < 2880
          ? "вчера"
          : `${Math.floor(minutes / 1440)} дн назад`;
}
export function usePresence(api: Api, logout: () => void) {
  const [users, setUsers] = useState<OnlineUser[]>([]);
  const [connected, setConnected] = useState(false);
  const [selfKey, setSelfKey] = useState("");
  useEffect(() => {
    let disposed = false,
      socket: WebSocket | null = null,
      attempt = 0,
      activityAt = 0,
      receivedAt = Date.now();
    let retry: ReturnType<typeof setTimeout>,
      heartbeat: ReturnType<typeof setInterval>;
    const activity = () => {
      if (
        socket?.readyState === WebSocket.OPEN &&
        Date.now() - activityAt >= 25000
      ) {
        socket.send(JSON.stringify({ type: "activity" }));
        activityAt = Date.now();
      }
    };
    const reconnect = () => {
      if (!disposed)
        retry = setTimeout(
          () => void connect(),
          Math.min(30000, 1000 * 2 ** Math.min(attempt++, 5)) +
            Math.random() * 500,
        );
    };
    async function connect() {
      try {
        const grant = await api.json<{
          ticket: string;
          heartbeat_seconds: number;
        }>("/presence/ws-ticket", { method: "POST" });
        if (disposed) return;
        const url = new URL(api.base + "/ws/presence");
        url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
        url.searchParams.set("ticket", grant.ticket);
        socket = new WebSocket(url);
        socket.onmessage = (event) => {
          receivedAt = Date.now();
          const data = JSON.parse(event.data);
          if (data.type === "snapshot") {
            setUsers(data.users);
            setSelfKey(data.self_key);
            setConnected(true);
            attempt = 0;
          } else if (data.user)
            setUsers((rows) => [
              ...rows.filter((r) => r.key !== data.user.key),
              data.user,
            ]);
        };
        socket.onopen = () => {
          receivedAt = Date.now();
          heartbeat = setInterval(() => {
            if (Date.now() - receivedAt > 75000) {
              socket?.close();
              return;
            }
            if (socket?.readyState === WebSocket.OPEN)
              socket.send(JSON.stringify({ type: "heartbeat" }));
          }, grant.heartbeat_seconds * 1000);
        };
        socket.onclose = () => {
          clearInterval(heartbeat);
          if (!disposed) {
            setConnected(false);
            reconnect();
          }
        };
        socket.onerror = () => socket?.close();
      } catch (e) {
        if (disposed) return;
        setConnected(false);
        if (e instanceof ApiError && e.status === 401) {
          logout();
          return;
        }
        reconnect();
      }
    }
    window.addEventListener("keydown", activity);
    window.addEventListener("pointerdown", activity);
    window.addEventListener("wheel", activity, { passive: true });
    void connect();
    return () => {
      disposed = true;
      clearTimeout(retry);
      clearInterval(heartbeat);
      socket?.close();
      window.removeEventListener("keydown", activity);
      window.removeEventListener("pointerdown", activity);
      window.removeEventListener("wheel", activity);
    };
  }, [api, logout]);
  return { users, connected, selfKey };
}
