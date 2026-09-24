// The update policy: version ordering (a downgrade is never an update), the binding rule that ties
// the offered version to the file the signature was made for, honest messages, and the rule that an
// install waits for a safe point. The signature itself is verified by the Rust updater plugin, which
// refuses a package that does not match the compiled-in public key.

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
// The committed signature, read from the fixture instead of written out here: `tauri signer sign`
// made it for the artifact file the manifest serves, and `src-tauri/tests/updater_signature.rs`
// verifies it against the public key compiled into the app. Keeping the accepted cases on that
// material means this file never proves a rule with a signature nobody would serve.
import SIGNATURE_FIXTURE from "../../src-tauri/tests/fixtures/updater/canalla-update-fixture.bin.sig?raw";

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
  rawJson: Record<string, unknown>;
  download: (onEvent?: (event: never) => void) => Promise<void>;
  install: () => Promise<void>;
}

// The committed signature is base64 of a minisign file: the client never verifies cryptography
// (the plugin does, at download time), it reads the trusted comment that the signature covers.
const REAL_SIGNATURE = SIGNATURE_FIXTURE.trim();
/** The artifact the committed signature was made for, and the version in that name. */
const SIGNED_ARTIFACT = "Canalla LLM_1.2.0_x64-setup.exe";
const SIGNED_VERSION = "1.2.0";

/** Minisign-*shaped* text for names no genuine signature can carry (an unversioned file, a missing
 * `file:` field). Generated, never presented as a signature: the shapes below cannot occur in one
 * produced by `tauri signer sign`, and the policy check reads the same comment either way. */
function shapeOnlySignatureNaming(name: string): string {
  return btoa(
    [
      "untrusted comment: signature from tauri secret key",
      "RUQkD9BSC/mft5d5lIlI4BhuIR/jnOC7NQd7o2AgT2XlC2t3wLXPss+2i7JjkUjqWzLcmPzgaskRpAgJHPxe0iuNbsiTZRSaegw=",
      `trusted comment: timestamp:1790225105\tfile:${name}`,
      "wzaONxjqF7i2dDCqP99YVlH44+QnmJ1QCfEoGEyNoJvN08TSV/tsrhsgCR2uNAG9ptDu18ZSpiObS4Uw0OcyBw==",
    ].join("\n") + "\n",
  );
}

/** The release file for a version, the way the release process names it. */
function artifactName(version: string): string {
  return `Canalla LLM_${version}_x64-setup.exe`;
}

/** The manifest the endpoint serves: bound to `version` unless a field is deliberately broken. */
function manifest(
  version: string,
  overrides: {
    /** The name inside the signature's trusted comment. */
    signedName?: string;
    /** The file the url serves; the same as the signed name unless a test breaks that. */
    urlName?: string;
    signature?: string;
  } = {},
): Record<string, unknown> {
  const signed = overrides.signedName ?? artifactName(version);
  const served = overrides.urlName ?? signed;
  return {
    version,
    notes: "Что нового",
    channel: "stable",
    platforms: {
      "windows-x86_64": {
        // Percent-encoded, exactly like the published manifest: the rule compares the name inside
        // the signature with the *decoded* basename.
        url: `https://gateway.12testers.store/downloads/${encodeURIComponent(served)}`,
        // The committed signature is used wherever it is the signature of the name in question;
        // anything else is a shape case (see `shapeOnlySignatureNaming`).
        signature:
          overrides.signature ??
          (signed === SIGNED_ARTIFACT
            ? REAL_SIGNATURE
            : shapeOnlySignatureNaming(signed)),
      },
    },
  };
}

function fakeUpdate(overrides: Partial<FakeUpdate> = {}): FakeUpdate {
  const version = overrides.version ?? "1.2.0";
  return {
    version,
    body: null,
    date: null,
    // A manifest that is bound to the version it declares: the default every fake starts from.
    rawJson: manifest(version),
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

  // The version lives in manifest metadata, but the *signature* covers the trusted comment that
  // names the artifact - and the release keeps the version in the file name. So an update may only
  // be offered when the signed name is the file the url serves and it carries the declared version.
  describe("the signed artifact must carry the version it is offered under", () => {
    it("offers a correctly bound newer release (the control: this rule does not disable updates)", async () => {
      // The accepted case carries the committed signature, over the artifact name its url serves.
      mockedCheck.mockResolvedValue(
        asUpdate(
          fakeUpdate({
            body: "Что нового",
            rawJson: manifest(SIGNED_VERSION, { signature: REAL_SIGNATURE }),
          }),
        ),
      );
      expect(await checkForUpdate()).toEqual({
        status: "available",
        current: "1.1.0",
        update: {
          version: SIGNED_VERSION,
          notes: "Что нового",
          publishedAt: null,
        },
      });
    });

    it("refuses a signature that names a file other than the one the url serves", async () => {
      // The genuine Windows signature, with the url serving the Linux package instead.
      mockedCheck.mockResolvedValue(
        asUpdate(
          fakeUpdate({
            rawJson: manifest(SIGNED_VERSION, {
              urlName: "Canalla LLM_1.2.0_amd64.deb",
              signature: REAL_SIGNATURE,
            }),
          }),
        ),
      );
      expect(await checkForUpdate()).toEqual({
        status: "refused",
        current: "1.1.0",
        version: SIGNED_VERSION,
        reason: "artifact_name_mismatch",
        message: expect.stringContaining("отклонено"),
      });
    });

    it("refuses a manifest version that is not the version in the signed name", async () => {
      mockedCheck.mockResolvedValue(
        asUpdate(
          fakeUpdate({
            version: "1.2.1",
            rawJson: manifest("1.2.1", {
              signedName: SIGNED_ARTIFACT,
              signature: REAL_SIGNATURE,
            }),
          }),
        ),
      );
      const result = await checkForUpdate();
      expect(result.status).toBe("refused");
      if (result.status === "refused") {
        expect(result.reason).toBe("version_mismatch");
        expect(result.version).toBe("1.2.1");
      }
    });

    it("refuses an old, validly signed artifact replayed as a newer release", async () => {
      // The attack this rule exists for: the signature is genuine, the bytes it covers are the ones
      // already released as 1.2.0, and the manifest offers them as 9.9.9.
      mockedCheck.mockResolvedValue(
        asUpdate(
          fakeUpdate({
            version: "9.9.9",
            rawJson: manifest("9.9.9", {
              urlName: artifactName("9.9.9"),
              signature: REAL_SIGNATURE,
            }),
          }),
        ),
      );
      const result = await checkForUpdate();
      expect(result.status).toBe("refused");
      if (result.status === "refused")
        expect(result.reason).toBe("artifact_name_mismatch");
    });

    it("refuses a signed name that carries no version at all", async () => {
      // Shape case: a signed name without a version can never be bound to one, so it is refused
      // instead of being offered as any release. No genuine signature carries such a name - the
      // committed one is made for the artifact file - so the text here is generated.
      mockedCheck.mockResolvedValue(
        asUpdate(
          fakeUpdate({
            rawJson: manifest(SIGNED_VERSION, {
              signedName: "canalla-update-fixture.bin",
            }),
          }),
        ),
      );
      const result = await checkForUpdate();
      expect(result.status).toBe("refused");
      if (result.status === "refused")
        expect(result.reason).toBe("version_unbound");
    });

    it("refuses a signature that does not decode or carries no trusted comment", async () => {
      const broken = [
        // A truncated committed signature is no longer base64 at all.
        REAL_SIGNATURE.slice(0, 21),
        "not base64 at all !!",
        "",
        btoa("trusted comment: timestamp:1790225105\tfile:x.exe"),
        btoa("untrusted comment: signature from tauri secret key\nRUQkD9BSC\n"),
      ];
      for (const signature of broken) {
        mockedCheck.mockResolvedValue(
          asUpdate(
            fakeUpdate({ rawJson: manifest(SIGNED_VERSION, { signature }) }),
          ),
        );
        const result = await checkForUpdate();
        expect(result.status).toBe("refused");
        if (result.status === "refused")
          expect(result.reason).toBe("signature_unreadable");
      }
    });

    it("refuses a signature whose trusted comment names no file", async () => {
      // Shape case: no signature `tauri signer sign` produces lacks the `file:` field, so the text is
      // generated - what it exercises is the refusal to treat an unnamed artifact as an update.
      mockedCheck.mockResolvedValue(
        asUpdate(
          fakeUpdate({
            rawJson: manifest(SIGNED_VERSION, {
              signature: btoa(
                "untrusted comment: signature from tauri secret key\nRUQkD9BSC\ntrusted comment: timestamp:1790225105\n",
              ),
            }),
          }),
        ),
      );
      const result = await checkForUpdate();
      expect(result.status).toBe("refused");
      if (result.status === "refused")
        expect(result.reason).toBe("signature_unnamed");
    });

    it("refuses a downgrade and keeps the install path closed", async () => {
      // A bound but older manifest is a stale mirror, not an attack: it is never offered, and
      // nothing can be installed because nothing was downloaded.
      mockedCheck.mockResolvedValue(
        asUpdate(fakeUpdate({ version: "1.0.0", rawJson: manifest("1.0.0") })),
      );
      expect(await checkForUpdate()).toEqual({
        status: "none",
        current: "1.1.0",
      });
      await expect(installAndRestart()).resolves.toEqual({
        ok: false,
        message: "Сначала скачайте обновление.",
      });
      expect(mockedRelaunch).not.toHaveBeenCalled();
    });

    it("never downloads a refused manifest", async () => {
      const download = vi.fn(async () => {});
      // The genuine signature, offered under the previous release's file name.
      mockedCheck.mockResolvedValue(
        asUpdate(
          fakeUpdate({
            rawJson: manifest(SIGNED_VERSION, {
              urlName: artifactName("1.1.0"),
              signature: REAL_SIGNATURE,
            }),
            download,
          }),
        ),
      );
      const result = await downloadUpdate(() => {});
      expect(result.ok).toBe(false);
      if (!result.ok) expect(result.message).toContain("отклонено");
      expect(download).not.toHaveBeenCalled();
      await expect(installAndRestart()).resolves.toEqual({
        ok: false,
        message: "Сначала скачайте обновление.",
      });
    });

    it("never downloads a downgrade either", async () => {
      const download = vi.fn(async () => {});
      mockedCheck.mockResolvedValue(
        asUpdate(
          fakeUpdate({
            version: "1.0.0",
            rawJson: manifest("1.0.0"),
            download,
          }),
        ),
      );
      await expect(downloadUpdate(() => {})).resolves.toEqual({
        ok: false,
        message: "Обновление больше не предлагается.",
      });
      expect(download).not.toHaveBeenCalled();
    });
  });
});
