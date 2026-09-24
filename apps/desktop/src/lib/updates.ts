// Updates: check asynchronously, download only when asked, install only on a confirmed action and
// never in the middle of work the user is waiting for.
//
// The cryptographic part is not here: the Tauri updater plugin verifies every downloaded package
// against the public key compiled into the app, and refuses anything that does not match. What this
// module owns is the *policy* around it - version ordering (a downgrade is never an update), the
// rule that the offered version must be the one the signature was made for, the deferral rules, and
// an honest state for the UI.

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

/** Why an offered manifest was refused: its artifact is not bound to the version it declares. */
export type UpdateRefusal =
  | "manifest_incomplete"
  | "signature_unreadable"
  | "signature_unnamed"
  | "artifact_name_mismatch"
  | "version_unbound"
  | "version_mismatch";

const REFUSAL_MESSAGES: Record<UpdateRefusal, string> = {
  manifest_incomplete:
    "Обновление отклонено: манифест не описывает пакет для этой платформы.",
  signature_unreadable: "Обновление отклонено: подпись пакета не читается.",
  signature_unnamed:
    "Обновление отклонено: подпись не называет пакет, который удостоверяет.",
  artifact_name_mismatch:
    "Обновление отклонено: имя в подписи не совпадает с адресом загрузки.",
  version_unbound:
    "Обновление отклонено: имя пакета в подписи не содержит версии.",
  version_mismatch: "Обновление отклонено: подпись выдана не для этой версии.",
};

/** A refusal is never reported as "no update": it is a typed, user-visible decision. */
export function refusalMessage(reason: UpdateRefusal): string {
  return REFUSAL_MESSAGES[reason];
}

// The artifact's identity is the name inside the signature's *trusted comment*: minisign covers that
// comment with the signature's global signature field, so the name there is authenticated material.
// The release keeps its version in the file name (`Canalla LLM_1.2.0_x64-setup.exe`), which is what
// stops a genuine signature for an old artifact from being advertised as a newer release: the
// declared version has to be the version the signature was made for.
const VERSION_IN_NAME = /\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.+-]*)?/g;

interface SignedArtifact {
  url: string;
  signature: string;
}

/** Every artifact the manifest describes, in both shapes the updater understands. */
function signedArtifacts(raw: unknown): SignedArtifact[] {
  const found: SignedArtifact[] = [];
  const collect = (node: unknown) => {
    if (!node || typeof node !== "object") return;
    const entry = node as Record<string, unknown>;
    if (typeof entry.url === "string" && typeof entry.signature === "string")
      found.push({ url: entry.url, signature: entry.signature });
  };
  collect(raw);
  const platforms =
    raw && typeof raw === "object"
      ? (raw as Record<string, unknown>).platforms
      : null;
  if (platforms && typeof platforms === "object")
    for (const entry of Object.values(platforms as Record<string, unknown>))
      collect(entry);
  return found;
}

/** The minisign signature file inside the base64 field, or `null` if it is not one. */
function decodeSignature(signature: string): string | null {
  try {
    const text = atob(signature);
    return text.startsWith("untrusted comment:") &&
      text.includes("\ntrusted comment:")
      ? text
      : null;
  } catch {
    return null;
  }
}

/** The `file:` field of the trusted comment: the artifact this signature was made for. */
function signedFileName(text: string): string | null {
  for (const line of text.split("\n")) {
    if (!line.startsWith("trusted comment:")) continue;
    for (const field of line.slice("trusted comment:".length).split("\t")) {
      const value = field.trim();
      if (!value.startsWith("file:")) continue;
      const name = value.slice("file:".length).trim();
      if (name) return name;
    }
  }
  return null;
}

/** The file the url serves: the percent-decoded basename of its path. */
function servedFileName(url: string): string | null {
  const path = url.split(/[?#]/)[0] ?? "";
  try {
    return decodeURIComponent(path.slice(path.lastIndexOf("/") + 1));
  } catch {
    return null;
  }
}

function sameVersion(a: Version | null, b: Version | null): boolean {
  if (!a || !b) return false;
  return (
    a.major === b.major &&
    a.minor === b.minor &&
    a.patch === b.patch &&
    a.suffix === b.suffix
  );
}

/** The typed reason this manifest may not be offered, or `null` when every artifact is bound. */
export function artifactRefusal(
  raw: unknown,
  version: string,
): UpdateRefusal | null {
  const artifacts = signedArtifacts(raw);
  if (artifacts.length === 0) return "manifest_incomplete";
  const declared = parseVersion(version);
  for (const artifact of artifacts) {
    const text = decodeSignature(artifact.signature);
    if (text === null) return "signature_unreadable";
    const name = signedFileName(text);
    if (!name) return "signature_unnamed";
    if (name !== servedFileName(artifact.url)) return "artifact_name_mismatch";
    const inName = name.match(VERSION_IN_NAME) ?? [];
    if (inName.length === 0) return "version_unbound";
    if (inName.some((token) => !sameVersion(parseVersion(token), declared)))
      return "version_mismatch";
  }
  return null;
}

export type UpdateCheck =
  | { status: "none"; current: string }
  | { status: "available"; current: string; update: UpdateInfo }
  | {
      status: "refused";
      current: string;
      version: string;
      reason: UpdateRefusal;
      message: string;
    }
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
    // The signature has to name the file the url serves, and that name has to carry this version.
    // Otherwise the version is unauthenticated metadata and an old signed package could be offered
    // as a newer release - so a manifest that fails the binding is refused, visibly, never ignored.
    const refusal = artifactRefusal(update.rawJson, update.version);
    if (refusal)
      return {
        status: "refused",
        current,
        version: update.version,
        reason: refusal,
        message: refusalMessage(refusal),
      };
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
    // Re-checked here because this is the last point before bytes are fetched: a manifest that
    // stopped being bound - or that is no longer newer - must not be downloaded either.
    const refusal = artifactRefusal(update.rawJson, update.version);
    if (refusal) return { ok: false, message: refusalMessage(refusal) };
    const current = await getVersion().catch(() => null);
    if (!isNewerVersion(update.version, current))
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
