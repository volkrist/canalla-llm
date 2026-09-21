import { describe, expect, it } from "vitest";
import { backendMessage, isTauriRuntime, type BackendRuntime } from "./backend";

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
