// The update state machine: one owner of the periodic check, so no component invents its own timer.
//
// Nothing here blocks startup: the first check runs after mount, the next one hours later, and a
// failure only lands in `message` - an app that cannot reach the update server keeps working.

import { useCallback, useEffect, useRef, useState } from "react";
import { isTauriRuntime } from "../lib/backend";
import {
  checkForUpdate,
  currentVersion,
  downloadUpdate,
  installAndRestart,
  installDecision,
  UPDATE_CHECK_INTERVAL_MS,
  type UpdateInfo,
} from "../lib/updates";

export type UpdatePhase =
  "idle" | "checking" | "available" | "downloading" | "ready" | "error";

export interface UpdateState {
  phase: UpdatePhase;
  current: string | null;
  available: UpdateInfo | null;
  progress: number;
  message: string;
  checkedAt: string | null;
}

const EMPTY: UpdateState = {
  phase: "idle",
  current: null,
  available: null,
  progress: 0,
  message: "",
  checkedAt: null,
};

export type UpdateStore = ReturnType<typeof useUpdates>;

export function useUpdates(options: { enabled: boolean; busy: boolean }) {
  const [state, setState] = useState<UpdateState>(EMPTY);
  const { enabled, busy } = options;
  const busyRef = useRef(busy);
  busyRef.current = busy;

  const checkNow = useCallback(async (): Promise<UpdateState> => {
    if (!isTauriRuntime()) return EMPTY;
    setState((prev) => ({ ...prev, phase: "checking", message: "" }));
    const version = await currentVersion();
    const result = await checkForUpdate();
    const next: UpdateState = {
      phase:
        result.status === "available"
          ? "available"
          : result.status === "error"
            ? "error"
            : "idle",
      current: version,
      available: result.status === "available" ? result.update : null,
      progress: 0,
      message: result.status === "error" ? result.message : "",
      checkedAt: new Date().toISOString(),
    };
    setState(next);
    return next;
  }, []);

  const download = useCallback(async () => {
    setState((prev) => ({
      ...prev,
      phase: "downloading",
      progress: 0,
      message: "",
    }));
    const result = await downloadUpdate((percent) =>
      setState((prev) => ({ ...prev, progress: percent })),
    );
    setState((prev) =>
      result.ok
        ? { ...prev, phase: "ready", progress: 100, message: "" }
        : { ...prev, phase: "error", message: result.message },
    );
  }, []);

  const install = useCallback(async () => {
    const decision = installDecision(busyRef.current, state.phase === "ready");
    if (!decision.allowed) {
      setState((prev) => ({ ...prev, message: decision.reason ?? "" }));
      return false;
    }
    const result = await installAndRestart();
    if (!result.ok)
      setState((prev) => ({
        ...prev,
        phase: "error",
        message: result.message,
      }));
    return result.ok;
  }, [state.phase]);

  useEffect(() => {
    if (!enabled || !isTauriRuntime()) return;
    let alive = true;
    void checkNow();
    const timer = window.setInterval(() => {
      if (alive) void checkNow();
    }, UPDATE_CHECK_INTERVAL_MS);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [enabled, checkNow]);

  return { state, checkNow, download, install };
}
