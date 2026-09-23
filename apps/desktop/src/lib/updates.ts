// Updates: check asynchronously, download only when asked, install only on a confirmed action and
// never in the middle of work the user is waiting for.
//
// The security-critical part is not here: the Tauri updater plugin verifies every downloaded package
// against the public key compiled into the app, and refuses anything that does not match. What this
// module owns is the *policy* around it - version ordering (a downgrade is never an update), the
// deferral rules, and an honest state for the UI.

import { getVersion } from "@tauri-apps/api/app";
import { check, type Update } from "@tauri-apps/plugin-updater";
import { relaunch } from "@tauri-apps/plugin-process";
import { isTauriRuntime } from "./backend";

export const UPDATE_CHANNEL = "stable";
/** A check every four hours is enough: the app is not a news feed, and the Gateway is shared. */
export const UPDATE_CHECK_INTERVAL_MS = 4 * 60 * 60 * 1000;

export interface Version {
  major: number;
  minor: number;
  patch: number;
  /** Anything after the numbers (`-beta.1`, `+build`). A pre-release is not newer than its release. */
  suffix: string;
}

/** `null` for anything that is not a plain numeric version: an unreadable version is never "newer". */
export function parseVersion(value: string | null | undefined): Version | null {
  if (!value) return null;
  const match = /^(\d+)\.(\d+)\.(\d+)(.*)$/.exec(value.trim());
  if (!match) return null;
  return {
    major: Number(match[1]),
    minor: Number(match[2]),
    patch: Number(match[3]),
    suffix: match[4] || "",
  };
}

/** Is `candidate` a real update over `current`? Downgrades, equal versions and garbled versions: no. */
export function isNewerVersion(
  candidate: string | null | undefined,
  current: string | null | undefined,
): boolean {
  const next = parseVersion(candidate);
  const now = parseVersion(current);
  if (!next || !now) return false;
  if (next.major !== now.major) return next.major > now.major;
  if (next.minor !== now.minor) return next.minor > now.minor;
  if (next.patch !== now.patch) return next.patch > now.patch;
  // Same numbers: a plain release beats a pre-release, otherwise it is not an update.
  return now.suffix !== "" && next.suffix === "";
}

export interface UpdateInfo {
  version: string;
  notes: string | null;
  publishedAt: string | null;
}

export type UpdateCheck =
  | { status: "none"; current: string }
  | { status: "available"; current: string; update: UpdateInfo }
  | { status: "unsupported"; reason: string }
  | { status: "error"; message: string };

export function updateMessage(error: unknown): string {
  const text = error instanceof Error ? error.message : String(error);
  if (/dns|connect|network|timed? ?out|unreachable|request/i.test(text))
    return "Сервер обновлений недоступен. Canalla продолжает работать.";
  if (/signature|verify|invalid/i.test(text))
    return "Подпись обновления не совпала: пакет отклонён.";
  return "Не удалось проверить обновления.";
}

/** The plugin's `check()` needs Tauri; a browser preview simply has no updater. */
export async function currentVersion(): Promise<string | null> {
  if (!isTauriRuntime()) return null;
  return getVersion();
}

/** Ask the endpoint once. A version that is not newer is reported as "no update", not offered. */
export async function checkForUpdate(): Promise<UpdateCheck> {
  if (!isTauriRuntime())
    return {
      status: "unsupported",
      reason: "Обновления доступны в установленном приложении.",
    };
  let current = "";
  try {
    current = await getVersion();
  } catch {
    return {
      status: "error",
      message: "Не удалось определить версию приложения.",
    };
  }
  try {
    const update = await check();
    if (!update) return { status: "none", current };
    if (!isNewerVersion(update.version, current)) {
      // The server offered something that is not an update for this installation: ignore it.
      return { status: "none", current };
    }
    return {
      status: "available",
      current,
      update: {
        version: update.version,
        notes: update.body ?? null,
        publishedAt: update.date ?? null,
      },
    };
  } catch (error) {
    return { status: "error", message: updateMessage(error) };
  }
}

/** Keep the plugin handle for the install step without leaking it into component state. */
let pending: Update | null = null;

export async function downloadUpdate(
  onProgress: (percent: number) => void,
): Promise<{ ok: true } | { ok: false; message: string }> {
  if (!isTauriRuntime())
    return { ok: false, message: "Обновления доступны в приложении." };
  try {
    const update = await check();
    if (!update)
      return { ok: false, message: "Обновление больше не предлагается." };
    let total = 0;
    let received = 0;
    await update.download((event) => {
      if (event.event === "Started") total = event.data.contentLength ?? 0;
      if (event.event === "Progress" && total > 0) {
        received += event.data.chunkLength;
        onProgress(Math.min(100, Math.round((received / total) * 100)));
      }
      if (event.event === "Finished") onProgress(100);
    });
    pending = update;
    return { ok: true };
  } catch (error) {
    return { ok: false, message: updateMessage(error) };
  }
}

/** Never install in the middle of work: the caller passes what the app is doing right now. */
export function installDecision(
  busy: boolean,
  downloaded: boolean,
): { allowed: boolean; reason: string | null } {
  if (!downloaded)
    return { allowed: false, reason: "Сначала скачайте обновление." };
  if (busy)
    return {
      allowed: false,
      reason: "Дождитесь завершения текущей операции или остановите её.",
    };
  return { allowed: true, reason: null };
}

export async function installAndRestart(): Promise<
  { ok: true } | { ok: false; message: string }
> {
  if (!isTauriRuntime())
    return { ok: false, message: "Обновления доступны в приложении." };
  if (!pending) return { ok: false, message: "Сначала скачайте обновление." };
  try {
    await pending.install();
    await relaunch();
    return { ok: true };
  } catch (error) {
    return { ok: false, message: updateMessage(error) };
  }
}

/** Test seam: the plugin handle is otherwise a module-private detail. */
export function setPendingUpdate(update: Update | null): void {
  pending = update;
}
