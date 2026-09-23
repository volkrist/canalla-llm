// «Запускать Canalla вместе с Windows»: the stored policy and the machine's actual state.
//
// The point of these tests is that a failed registry write can never be shown as a success: the
// toggle renders what the operating system reports, and a launch reconciles the two directions
// (default on registers a fresh installation, an explicit off removes a surviving entry).

import { beforeEach, describe, expect, it, vi } from "vitest";
import { invoke } from "@tauri-apps/api/core";
import { isTauriRuntime } from "./backend";
import {
  autostartDecision,
  autostartError,
  autostartLabel,
  readAutostart,
  reconcileAutostart,
  writeAutostart,
  UNSUPPORTED,
  type AutostartStatus,
} from "./autostart";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));
vi.mock("./backend", () => ({ isTauriRuntime: vi.fn(() => true) }));

const mockedInvoke = vi.mocked(invoke);
const mockedRuntime = vi.mocked(isTauriRuntime);

function status(overrides: Partial<AutostartStatus> = {}): AutostartStatus {
  return {
    supported: true,
    enabled: false,
    command: null,
    error: null,
    ...overrides,
  };
}

describe("what a launch owes the machine", () => {
  it("registers a fresh installation, because the default is on", () => {
    expect(autostartDecision(true, status({ enabled: false }))).toBe(
      "register",
    );
  });

  it("removes an entry the user turned off", () => {
    expect(
      autostartDecision(
        false,
        status({ enabled: true, command: '"C:\\app\\alex-llm.exe"' }),
      ),
    ).toBe("unregister");
  });

  it("does nothing when the machine already matches the setting", () => {
    expect(autostartDecision(true, status({ enabled: true }))).toBe("none");
    expect(autostartDecision(false, status({ enabled: false }))).toBe("none");
  });

  it("never touches a build that does not support it, or a machine it cannot read", () => {
    expect(autostartDecision(true, UNSUPPORTED)).toBe("unsupported");
    expect(autostartDecision(true, null)).toBe("unsupported");
    expect(
      autostartDecision(true, status({ error: "autostart_registry_failed:5" })),
    ).toBe("unsupported");
  });
});

describe("reading and writing the registration", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedRuntime.mockReturnValue(true);
  });

  it("reads the state from the machine", async () => {
    mockedInvoke.mockResolvedValue(
      status({ enabled: true, command: '"C:\\app\\alex-llm.exe"' }),
    );
    await expect(readAutostart()).resolves.toEqual(
      status({ enabled: true, command: '"C:\\app\\alex-llm.exe"' }),
    );
    expect(mockedInvoke).toHaveBeenCalledWith("autostart_status");
  });

  it("reports a registry that cannot be read instead of pretending it is off", async () => {
    mockedInvoke.mockRejectedValueOnce(new Error("no registry"));
    const result = await readAutostart();
    expect(result.supported).toBe(true);
    expect(result.enabled).toBe(false);
    expect(result.error).toBe("autostart_registry_failed");
  });

  it("asks the machine what it has after a refused write", async () => {
    mockedInvoke
      .mockRejectedValueOnce(new Error("autostart_not_applied"))
      .mockResolvedValueOnce(status({ enabled: false }));
    const result = await writeAutostart(true);

    expect(result.error).toBe("autostart_not_applied");
    expect(result.status.enabled).toBe(false);
    expect(mockedInvoke).toHaveBeenLastCalledWith("autostart_status");
  });

  it("returns the confirmed state on success", async () => {
    mockedInvoke.mockResolvedValueOnce(
      status({ enabled: true, command: '"C:\\app\\alex-llm.exe"' }),
    );
    const result = await writeAutostart(true);

    expect(result.error).toBeNull();
    expect(result.status.enabled).toBe(true);
    expect(mockedInvoke).toHaveBeenCalledWith("set_autostart", {
      enabled: true,
    });
  });

  it("has no autostart outside the installed app", async () => {
    mockedRuntime.mockReturnValue(false);
    await expect(readAutostart()).resolves.toEqual(UNSUPPORTED);
    await expect(writeAutostart(true)).resolves.toEqual({
      status: UNSUPPORTED,
      error: "autostart_unavailable",
    });
    expect(mockedInvoke).not.toHaveBeenCalled();
  });
});

describe("reconciliation on startup", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedRuntime.mockReturnValue(true);
  });

  it("registers the product when the machine has nothing", async () => {
    mockedInvoke
      .mockResolvedValueOnce(status({ enabled: false }))
      .mockResolvedValueOnce(
        status({ enabled: true, command: '"C:\\app\\alex-llm.exe"' }),
      );

    const result = await reconcileAutostart(true);

    expect(mockedInvoke).toHaveBeenNthCalledWith(2, "set_autostart", {
      enabled: true,
    });
    expect(result.enabled).toBe(true);
  });

  it("removes an entry the user had turned off, without asking again", async () => {
    mockedInvoke
      .mockResolvedValueOnce(
        status({ enabled: true, command: '"C:\\app\\alex-llm.exe"' }),
      )
      .mockResolvedValueOnce(status({ enabled: false }));

    const result = await reconcileAutostart(false);

    expect(mockedInvoke).toHaveBeenNthCalledWith(2, "set_autostart", {
      enabled: false,
    });
    expect(result.enabled).toBe(false);
  });

  it("does not touch a registration that already matches", async () => {
    mockedInvoke.mockResolvedValueOnce(status({ enabled: true }));

    await reconcileAutostart(true);

    expect(mockedInvoke).toHaveBeenCalledTimes(1);
  });
});

describe("honest messages", () => {
  it("names each refusal for what it is", () => {
    expect(autostartError("autostart_unsupported")).toContain(
      "установленном приложении",
    );
    expect(autostartError("autostart_not_removed")).toContain(
      "не подтвердил удаление",
    );
    expect(autostartError("autostart_not_applied")).toContain(
      "не подтвердил запись",
    );
    expect(autostartError("something else")).toBe(
      "Не удалось изменить автозапуск Windows.",
    );
  });

  it("describes the state in one word for the toggle", () => {
    expect(autostartLabel(status({ enabled: true }))).toBe("Включён");
    expect(autostartLabel(status({ enabled: false }))).toBe("Выключен");
    expect(autostartLabel(UNSUPPORTED)).toBe("Недоступно");
    expect(autostartLabel(status({ error: "x" }))).toBe("Состояние неизвестно");
  });
});
