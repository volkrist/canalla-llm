// The update policy: version ordering (a downgrade is never an update), honest messages, and the
// rule that an install waits for a safe point. The signature check itself lives in the Rust
// updater plugin, which refuses a package that does not match the compiled-in public key.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getVersion } from "@tauri-apps/api/app";
import { relaunch } from "@tauri-apps/plugin-process";
import { check, type Update } from "@tauri-apps/plugin-updater";
import { isTauriRuntime } from "./backend";
import {
  busyKinds,
  clearBusy,
  isBusy,
  markBusy,
  resetBusy,
  subscribeBusy,
} from "./busy";
import {
  checkForUpdate,
  downloadUpdate,
  installAndRestart,
  installDecision,
  isNewerVersion,
  parseVersion,
  setPendingUpdate,
  updateMessage,
} from "./updates";

// The endpoint, the download and the install are the plugin's job; these tests own the policy
// around it and prove the app keeps running when the update server does not answer.
vi.mock("@tauri-apps/plugin-updater", () => ({ check: vi.fn() }));
vi.mock("@tauri-apps/plugin-process", () => ({ relaunch: vi.fn() }));
vi.mock("@tauri-apps/api/app", () => ({ getVersion: vi.fn() }));
vi.mock("./backend", () => ({ isTauriRuntime: vi.fn(() => true) }));

describe("version ordering", () => {
  it("reads plain numeric versions and rejects everything else", () => {
    expect(parseVersion("1.2.0")).toEqual({
      major: 1,
      minor: 2,
      patch: 0,
      suffix: "",
    });
    expect(parseVersion(" 1.10.3 ")).toMatchObject({ minor: 10, patch: 3 });
    expect(parseVersion("1.2.0-beta.1")).toMatchObject({ suffix: "-beta.1" });
    expect(parseVersion("1.2")).toBeNull();
    expect(parseVersion("v1.2.0")).toBeNull();
    expect(parseVersion("")).toBeNull();
    expect(parseVersion(null)).toBeNull();
  });

  it("accepts a newer version and refuses equal, older or unreadable ones", () => {
    expect(isNewerVersion("1.2.0", "1.1.0")).toBe(true);
    expect(isNewerVersion("1.1.1", "1.1.0")).toBe(true);
    expect(isNewerVersion("2.0.0", "1.9.9")).toBe(true);
    expect(isNewerVersion("1.1.0", "1.1.0")).toBe(false);
    expect(isNewerVersion("1.0.9", "1.1.0")).toBe(false);
    expect(isNewerVersion("1.1.0", "1.1.1")).toBe(false);
    expect(isNewerVersion("not-a-version", "1.1.0")).toBe(false);
    expect(isNewerVersion("1.2.0", "")).toBe(false);
  });

  it("never offers a pre-release over the release it precedes", () => {
    expect(isNewerVersion("1.2.0-beta.1", "1.1.0")).toBe(true);
    expect(isNewerVersion("1.2.0", "1.2.0-beta.1")).toBe(true);
    expect(isNewerVersion("1.2.0-beta.2", "1.2.0-beta.1")).toBe(false);
  });
});

describe("update messages", () => {
  it("keeps a network failure a state, not a scare", () => {
    expect(updateMessage(new Error("error sending request for url"))).toContain(
      "недоступен",
    );
    expect(updateMessage(new Error("connect timed out"))).toContain(
      "продолжает работать",
    );
  });

  it("names a signature mismatch for what it is", () => {
    expect(updateMessage(new Error("signature verification failed"))).toContain(
      "Подпись",
    );
    expect(updateMessage(new Error("invalid minisign signature"))).toContain(
      "отклонён",
    );
  });

  it("falls back to a neutral message", () => {
    expect(updateMessage(new Error("weird"))).toBe(
      "Не удалось проверить обновления.",
    );
    expect(updateMessage(undefined)).toBe("Не удалось проверить обновления.");
  });
});

describe("install deferral", () => {
  it("refuses before the package is downloaded", () => {
    const decision = installDecision(false, false);
    expect(decision.allowed).toBe(false);
    expect(decision.reason).toContain("Сначала скачайте");
  });

  it("refuses while the app is doing something the user waits for", () => {
    const decision = installDecision(true, true);
    expect(decision.allowed).toBe(false);
    expect(decision.reason).toContain("Дождитесь");
  });

  it("allows exactly the confirmed, idle case", () => {
    expect(installDecision(false, true)).toEqual({
      allowed: true,
      reason: null,
    });
  });
});

describe("busy registry", () => {
  it("is idle until something marks itself, and idle again after it clears", () => {
    resetBusy();
    const seen: boolean[] = [];
    const stop = subscribeBusy((value) => seen.push(value));

    expect(isBusy()).toBe(false);
    markBusy("generation");
    markBusy("generation");
    markBusy("backup");
    expect(isBusy()).toBe(true);
    expect(busyKinds().sort()).toEqual(["backup", "generation"]);
    clearBusy("backup");
    expect(isBusy()).toBe(true);
    clearBusy("generation");
    expect(isBusy()).toBe(false);

    stop();
    resetBusy();
    // Idempotent marks stay silent, a second holder still republishes (the set changed) and the
    // last release publishes the idle state.
    expect(seen).toEqual([true, true, true, false]);
  });
});

const mockedCheck = vi.mocked(check);
const mockedRelaunch = vi.mocked(relaunch);
const mockedVersion = vi.mocked(getVersion);
const mockedRuntime = vi.mocked(isTauriRuntime);

interface FakeUpdate {
  version: string;
  body: string | null;
  date: string | null;
  download: (onEvent?: (event: never) => void) => Promise<void>;
  install: () => Promise<void>;
}

function fakeUpdate(overrides: Partial<FakeUpdate> = {}): FakeUpdate {
  return {
    version: "1.2.0",
    body: null,
    date: null,
    download: async () => {},
    install: async () => {},
    ...overrides,
  };
}

function asUpdate(value: FakeUpdate | null): Update | null {
  return value as unknown as Update | null;
}

describe("the updater against the endpoint", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedRuntime.mockReturnValue(true);
    mockedVersion.mockResolvedValue("1.1.0");
    setPendingUpdate(null);
  });

  afterEach(() => {
    setPendingUpdate(null);
  });

  it("has no updater in a browser preview", async () => {
    mockedRuntime.mockReturnValue(false);
    expect(await checkForUpdate()).toEqual({
      status: "unsupported",
      reason: "Обновления доступны в установленном приложении.",
    });
    expect(await downloadUpdate(() => {})).toEqual({
      ok: false,
      message: "Обновления доступны в приложении.",
    });
  });

  it("offers a newer version and keeps what the release notes say", async () => {
    mockedCheck.mockResolvedValue(
      asUpdate(
        fakeUpdate({ body: "Что нового", date: "2026-09-23T00:00:00Z" }),
      ),
    );
    expect(await checkForUpdate()).toEqual({
      status: "available",
      current: "1.1.0",
      update: {
        version: "1.2.0",
        notes: "Что нового",
        publishedAt: "2026-09-23T00:00:00Z",
      },
    });
  });

  it("reports no update for an empty answer and for the version already running", async () => {
    mockedCheck.mockResolvedValue(null);
    expect(await checkForUpdate()).toEqual({
      status: "none",
      current: "1.1.0",
    });

    mockedCheck.mockResolvedValue(asUpdate(fakeUpdate({ version: "1.1.0" })));
    expect(await checkForUpdate()).toEqual({
      status: "none",
      current: "1.1.0",
    });
  });

  it("never offers a downgrade a tampered endpoint might advertise", async () => {
    mockedCheck.mockResolvedValue(asUpdate(fakeUpdate({ version: "1.0.0" })));
    expect(await checkForUpdate()).toEqual({
      status: "none",
      current: "1.1.0",
    });
  });

  it("keeps the app running when the update server cannot be reached", async () => {
    const failures = [
      "error sending request for url (https://gateway/updates/latest): operation timed out",
      "error sending request for url (https://gateway/updates/latest): dns error",
      "connect error: network is unreachable",
    ];
    for (const message of failures) {
      mockedCheck.mockRejectedValue(new Error(message));
      expect(await checkForUpdate()).toEqual({
        status: "error",
        message: expect.stringContaining("продолжает работать"),
      });
    }
  });

  it("treats 404, 500 and an unreadable body as one honest failure", async () => {
    mockedCheck.mockRejectedValue(
      new Error("Could not fetch a valid release JSON from the remote"),
    );
    expect(await checkForUpdate()).toEqual({
      status: "error",
      message: "Не удалось проверить обновления.",
    });
  });

  it("refuses a package whose signature does not verify", async () => {
    mockedCheck.mockResolvedValue(
      asUpdate(
        fakeUpdate({
          download: async () => {
            throw new Error(
              "The signature dG50 could not be decoded, please check if it is a valid base64 string.",
            );
          },
        }),
      ),
    );
    const result = await downloadUpdate(() => {});
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.message).toContain("Подпись");
  });

  it("does not install before a package was downloaded", async () => {
    setPendingUpdate(null);
    await expect(installAndRestart()).resolves.toEqual({
      ok: false,
      message: "Сначала скачайте обновление.",
    });
    expect(mockedRelaunch).not.toHaveBeenCalled();
  });

  it("reports progress, then installs and restarts on the confirmed action", async () => {
    const progress: number[] = [];
    const install = vi.fn(async () => {});
    mockedCheck.mockResolvedValue(
      asUpdate(
        fakeUpdate({
          download: async (onEvent) => {
            onEvent?.({
              event: "Started",
              data: { contentLength: 200 },
            } as never);
            onEvent?.({
              event: "Progress",
              data: { chunkLength: 50 },
            } as never);
            onEvent?.({ event: "Finished", data: {} } as never);
          },
          install,
        }),
      ),
    );

    await expect(
      downloadUpdate((percent) => progress.push(percent)),
    ).resolves.toEqual({
      ok: true,
    });
    expect(progress.at(-1)).toBe(100);

    await expect(installAndRestart()).resolves.toEqual({ ok: true });
    expect(install).toHaveBeenCalledTimes(1);
    expect(mockedRelaunch).toHaveBeenCalledTimes(1);
  });
});
