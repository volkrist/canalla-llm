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
  // A failed read leaves the last snapshot in place for the balance (which has its own timestamp),
  // but that snapshot is no longer a statement about health: `stale` keeps the chips from showing
  // an old «Готово» as if it were current.
  const [stale, setStale] = useState(false);

  const request = useCallback(async () => {
    const next = await fetchStatus(api);
    latest.current = next;
    setSnapshot(next);
    setError("");
    setStale(false);
    return next;
  }, [api]);

  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    let timer: number | undefined;
    let inFlight = false;
    const delay = () =>
      statusDelaySeconds(latest.current, document.hidden) * 1000;
    const tick = async () => {
      if (inFlight || !alive) return;
      inFlight = true;
      try {
        await request();
      } catch {
        if (alive) {
          setSnapshot(latest.current);
          setStale(true);
          setError("Не удалось получить состояние");
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
      setStale(true);
      setError("Не удалось получить состояние");
    } finally {
      setRefreshing(false);
    }
  }, [request]);

  return { snapshot, error, refresh, refreshing, stale };
}
