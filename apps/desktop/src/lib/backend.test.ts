import { describe, expect, it, vi } from "vitest";
import {
  backendMessage,
  isTauriRuntime,
  recoverBackend,
  recoveryDelay,
  RECOVERY_DELAYS_MS,
  type BackendRuntime,
} from "./backend";

const ready: BackendRuntime = {
  state: "ready",
  ownership: "owned",
  url: "http://127.0.0.1:8000",
  port: 8000,
  data_dir: "C:\\\\Users\\\\x\\\\AppData\\\\Local\\\\Alex LLM",
};

describe("backend runtime copy", () => {
  it("does not treat the browser preview as Tauri", () => {
    expect(isTauriRuntime()).toBe(false);
  });
  it("hides the banner when owned backend is ready", () => {
    expect(backendMessage(ready)).toBe("");
  });
  it("explains external reuse and migration failure", () => {
    expect(backendMessage({ ...ready, ownership: "external" })).toContain(
      "уже запущенному",
    );
    expect(
      backendMessage({
        ...ready,
        state: "error",
        ownership: "none",
        error: "MIGRATION_FAILED",
      }),
    ).toContain("данные не удалены");
    expect(
      backendMessage({
        ...ready,
        state: "error",
        ownership: "none",
        error: "BACKEND_SIDECAR_MISSING",
      }),
    ).toContain("Переустановите");
  });
});

describe("crashed local backend recovery", () => {
  it("is bounded: the attempts run out instead of looping", () => {
    expect(recoveryDelay(0)).toBe(0);
    expect(recoveryDelay(RECOVERY_DELAYS_MS.length - 1)).toBeGreaterThanOrEqual(
      0,
    );
    expect(recoveryDelay(RECOVERY_DELAYS_MS.length)).toBeNull();
    expect(recoveryDelay(-1)).toBeNull();
    expect(recoveryDelay(1.5)).toBeNull();
  });

  it("asks the Desktop to re-ensure the backend, then for an explicit restart", async () => {
    const ensure = vi.fn(async () => ({
      ...ready,
      url: "http://127.0.0.1:8001",
    }));
    const restart = vi.fn(async () => ({ ...ready, state: "ready" as const }));
    const sleep = vi.fn(async () => {});

    const first = await recoverBackend(0, { ensure, restart, sleep });
    const second = await recoverBackend(1, { ensure, restart, sleep });

    expect(first).toEqual({ attempted: true, state: "ready" });
    expect(second).toEqual({ attempted: true, state: "ready" });
    expect(ensure).toHaveBeenCalledTimes(1);
    expect(restart).toHaveBeenCalledTimes(1);
    expect(sleep).toHaveBeenCalledWith(RECOVERY_DELAYS_MS[1]);
  });

  it("a failed attempt is reported, not thrown, and never fakes readiness", async () => {
    const ensure = vi.fn(async () => {
      throw new Error("no sidecar");
    });
    const restart = vi.fn(async () => {
      throw new Error("no sidecar");
    });

    const result = await recoverBackend(0, {
      ensure,
      restart,
      sleep: async () => {},
    });

    expect(result).toEqual({ attempted: true, state: null });
    expect(
      await recoverBackend(99, { ensure, restart, sleep: async () => {} }),
    ).toEqual({
      attempted: false,
      state: null,
    });
  });

  it("never waits forever on one command", async () => {
    const stuck = vi.fn(() => new Promise<never>(() => {}));

    const result = await recoverBackend(0, {
      ensure: stuck,
      sleep: async () => {},
      timeoutMs: 30,
    });

    expect(result).toEqual({ attempted: true, state: null });
    expect(stuck).toHaveBeenCalledTimes(1);
  });

  it("a slow success still wins when it arrives inside the budget", async () => {
    const slow = vi.fn(
      () =>
        new Promise<BackendRuntime>((done) =>
          setTimeout(() => done({ ...ready, pid: 4242 }), 20),
        ),
    );

    const result = await recoverBackend(0, {
      ensure: slow,
      sleep: async () => {},
      timeoutMs: 500,
    });

    expect(result).toEqual({ attempted: true, state: "ready" });
  });
});
