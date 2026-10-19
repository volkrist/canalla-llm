import { useCallback, useEffect, useRef, useState } from "react";
import type { Api } from "../lib/api";
import {
  fetchStatus,
  statusDelaySeconds,
  type StatusSnapshot,
} from "../lib/status";

/**
 * The single owner of the status polling lifecycle. One self-scheduling timer per
 * mount, no parallel requests, and it slows down while the window is hidden.
 */
export function useStatus(api: Api, enabled: boolean) {
  const [snapshot, setSnapshot] = useState<StatusSnapshot | null>(null);
  const [error, setError] = useState("");
  const latest = useRef<StatusSnapshot | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  // A single failed poll is a blink, not a verdict. The last snapshot stays authoritative while
  // the client retries; only a few consecutive failures turn it into `stale` (history) and show
  // the recovery alert. Blanking every chip on the first miss is what made the whole status bar
  // flap between «Готово» and «Проверяем…».
  const [stale, setStale] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const failures = useRef(0);
  const STALE_AFTER = 2;

  const request = useCallback(async () => {
    const next = await fetchStatus(api);
    latest.current = next;
    failures.current = 0;
    setSnapshot(next);
    setError("");
    setStale(false);
    setReconnecting(false);
    return next;
  }, [api]);

  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    let timer: number | undefined;
    let inFlight = false;
    const delay = () => {
      // One miss gets a fast retry (a real outage answers that retry too), so a single transient
      // failure neither blanks the bar nor waits a whole polling interval to recover.
      if (failures.current === 1) return 1000;
      const base = statusDelaySeconds(latest.current, document.hidden);
      // While the endpoint is down, keep retrying on a short bounded cadence: a recovered backend
      // must clear `stale` quickly, not after an exponential minute.
      if (failures.current >= STALE_AFTER) return Math.min(base, 10) * 1000;
      return base * 1000;
    };
    const tick = async () => {
      if (inFlight || !alive) return;
      inFlight = true;
      try {
        await request();
      } catch {
        if (alive) {
          failures.current += 1;
          setSnapshot(latest.current);
          setReconnecting(true);
          if (failures.current >= STALE_AFTER) {
            setStale(true);
            setError("Не удалось получить состояние");
          }
        }
      } finally {
        inFlight = false;
        if (alive) {
          window.clearTimeout(timer);
          timer = window.setTimeout(() => void tick(), delay());
        }
      }
    };
    void tick();
    const onVisible = () => {
      if (!document.hidden) void tick();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      alive = false;
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [api, enabled, request]);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await request();
    } catch {
      failures.current += 1;
      setReconnecting(true);
      if (failures.current >= STALE_AFTER) {
        setStale(true);
        setError("Не удалось получить состояние");
      }
    } finally {
      setRefreshing(false);
    }
  }, [request]);

  return { snapshot, error, refresh, refreshing, stale, reconnecting };
}
