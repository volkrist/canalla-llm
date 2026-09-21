import { useEffect, useSyncExternalStore } from "react";
import type { Api } from "./api";

/** The kind a backup folder was created with. `null` means the folder carries no
 *  usable manifest, so no kind is claimed for it. */
export type BackupKind = "manual" | "pre_upgrade" | "pre_restore" | null;

/** One row of `GET /backup`. `path` belongs to the payload but is never rendered:
 *  React shows no file-system location, only the backups root as plain text. */
export interface BackupSummary {
  id: string;
  path: string;
  kind: BackupKind;
  label: string;
  created_at: string | null;
  app_version: string | null;
  schema_revision: string | null;
  files: number;
  bytes: number;
  verified: boolean;
  verified_at: string | null;
  complete: boolean;
  problem: string | null;
}

/** The answer of `POST /backup` and `POST /backup/verify`: the backend hashed and
 *  checked what it wrote, so this is a statement about real bytes on disk. */
export interface BackupVerification {
  id: string;
  path: string;
  verified: boolean;
  verified_at: string | null;
  backup_format_version: number | null;
  app_version: string | null;
  schema_revision: string | null;
  created_at: string | null;
  files: number;
  bytes: number;
  documents: number;
  kind: BackupKind;
  label: string;
}

/** `last_restore` as the backend recorded it. Restore is a Desktop operation, so
 *  this is a report about a finished run and not a live state. */
export interface BackupRestoreRecord {
  restored: boolean;
  backup_id?: string;
  safety_backup_id?: string;
  from_revision?: string | null;
  to_revision?: string | null;
  documents?: number;
  rolled_back?: boolean;
  at?: string;
  code?: string;
  message?: string;
}

/** `GET /backup`. `state.busy` is non-empty while the backend already runs a backup
 *  operation, so the panel disables its controls instead of racing it. */
export interface BackupList {
  state: {
    busy: string;
    last_result: BackupVerification | null;
    backups_root: string;
    format_version: number;
  };
  backups: BackupSummary[];
  last_restore: BackupRestoreRecord | null;
  keep: { automatic: number; manual: number };
}

/** `GET /backup/diagnostic`. Read-only facts about the installation, and nothing in
 *  it is a credential; the panel deliberately renders none of it. */
export interface BackupDiagnostic {
  app_version: string;
  backup_format_version: number;
  schema_revision: string | null;
  head_revision: string | null;
  migration_pending: boolean;
  last_migration: unknown;
  backups_root: string;
  backups_count: number;
  verified_count: number;
  last_backup_at: string | null;
  last_verified_at: string | null;
  disk_free_bytes: number;
  keep_automatic: number;
  keep_manual: number;
  mode: string;
  restore_supported: boolean;
}

/** The answer of the Desktop `restore_backup` command. A non-throwing answer with
 *  `restored: false` is a failure and its `message` is shown as it is. */
export interface RestoreResult {
  restored: boolean;
  code: string;
  message: string;
  backup_id?: string;
  safety_backup_id?: string;
  to_revision?: string;
  at?: string;
}

const KIND_LABELS: Record<string, string> = {
  manual: "Ручная",
  pre_upgrade: "Перед обновлением",
  pre_restore: "Перед восстановлением",
};

/** Russian name of a backup kind, or an honest "unknown" for a folder without a
 *  readable manifest. */
export function kindLabel(kind: BackupKind): string {
  return (kind && KIND_LABELS[kind]) || "Неизвестно";
}

/** A row never claims more than the manifest and the verification marker proved. */
export function backupStatusLabel(summary: BackupSummary): string {
  if (!summary.complete) return "Повреждена";
  return summary.verified ? "Проверена" : "Не проверена";
}

/** Sizes are shown to the user, so they are decimal-comma Russian units. */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "размер неизвестен";
  const units = ["Б", "КБ", "МБ", "ГБ", "ТБ"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const text =
    unit === 0 ? String(value) : value.toFixed(1).replace(/\.0$/, "");
  return `${text.replace(".", ",")} ${units[unit]}`;
}

/** `created_at` is an ISO timestamp from the backend; a missing or unparsable value
 *  is reported as unknown instead of a fake date. */
export function formatDate(value: string | null): string {
  if (!value) return "дата неизвестна";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "дата неизвестна";
  return date.toLocaleString("ru-RU", {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

/** Russian plural form for a count: 1 копия, 3 копии, 5 копий. */
export function plural(
  count: number,
  one: string,
  few: string,
  many: string,
): string {
  const mod10 = Math.abs(count) % 10;
  const mod100 = Math.abs(count) % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

/** Shared snapshot. A list that was already read stays visible after a failed later
 *  read: those files are still on disk, so pretending there are none would lie. */
export interface BackupSnapshot {
  list: BackupList | null;
  /** Set when the last read or operation failed. */
  error: string;
}

const EMPTY: BackupSnapshot = { list: null, error: "" };
const NO_SESSION = "Резервные копии доступны после входа в аккаунт.";
const UNREAD = "Не удалось прочитать список резервных копий";

let snapshot: BackupSnapshot = EMPTY;
const listeners = new Set<() => void>();
let client: Api | null = null;
let read: Promise<BackupSnapshot> | null = null;

function publish(next: BackupSnapshot) {
  snapshot = next;
  for (const listener of listeners) listener();
}

export function backupSnapshot(): BackupSnapshot {
  return snapshot;
}

export function subscribeBackup(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** App builds the single authenticated Api; the props-free panel must not build a
 *  second HTTP layer, so the Settings dialog hands that client over. A new session
 *  (or logout) clears the snapshot instead of carrying it over. */
export function setBackupClient(api: Api | null): void {
  if (api === client && api !== null) return;
  client = api;
  publish(EMPTY);
}

function errorText(error: unknown, fallback: string): string {
  if (error instanceof Error) return error.message.trim() || fallback;
  if (typeof error === "string" && error.trim()) return error.trim();
  return fallback;
}

/** One authoritative read. A failed read keeps the previous rows and reports why. */
async function readBackups(): Promise<BackupSnapshot> {
  if (!client) {
    publish({ list: null, error: NO_SESSION });
    return snapshot;
  }
  try {
    publish({ list: await client.json<BackupList>("/backup"), error: "" });
  } catch (error) {
    publish({ list: snapshot.list, error: errorText(error, UNREAD) });
  }
  return snapshot;
}

/** One read at a time: the dialog and the panel share the same request. */
export function refreshBackups(): Promise<BackupSnapshot> {
  if (!read) {
    read = readBackups().finally(() => {
      read = null;
    });
  }
  return read;
}

function requireClient(): Api {
  if (!client) throw new Error(NO_SESSION);
  return client;
}

/** A failed operation is recorded in the snapshot — the panel shows the API's own
 *  Russian message — and rethrown to the caller. */
function report(error: unknown, fallback: string): never {
  publish({ list: snapshot.list, error: errorText(error, fallback) });
  throw error;
}

/** Create one verified backup. The label is free text and never a credential; the
 *  backend verifies what it wrote before it answers. */
export async function createBackup(label = ""): Promise<BackupVerification> {
  try {
    const result = await requireClient().json<BackupVerification>("/backup", {
      method: "POST",
      body: JSON.stringify({ label }),
    });
    // The copy exists now: the list is re-read from the backend, never guessed.
    await readBackups();
    return result;
  } catch (error) {
    return report(error, "Не удалось создать резервную копию");
  }
}

/** Deep verification of one backup: the badge in the list follows this answer. */
export async function verifyBackup(
  backupId: string,
): Promise<BackupVerification> {
  try {
    const result = await requireClient().json<BackupVerification>(
      "/backup/verify",
      { method: "POST", body: JSON.stringify({ backup_id: backupId }) },
    );
    await readBackups();
    return result;
  } catch (error) {
    // A failed verification changes the row's state on the backend (the marker is
    // invalidated), so the list has to be re-read before the failure is reported.
    await readBackups().catch(() => undefined);
    return report(error, "Не удалось проверить резервную копию");
  }
}

async function invokeCommand<T>(
  command: string,
  args?: Record<string, unknown>,
): Promise<T> {
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<T>(command, args);
}

/** `restored: false` carries a Russian message; a failure code is only a fallback. */
function restoreFailure(result: RestoreResult): string {
  const message =
    typeof result.message === "string" ? result.message.trim() : "";
  if (message) return message;
  const code = typeof result.code === "string" ? result.code.trim() : "";
  return code
    ? `Восстановление не выполнено (${code}).`
    : "Восстановление не выполнено.";
}

/** Restore is never an HTTP call: the Desktop command stops the owned backend, swaps
 *  the data and starts the backend again. A non-throwing `restored: false` is a
 *  failure and is reported as such. */
export async function restoreBackup(backupId: string): Promise<RestoreResult> {
  try {
    const result = await invokeCommand<RestoreResult | null>("restore_backup", {
      backupId,
    });
    if (!result || result.restored !== true) {
      const message = result
        ? restoreFailure(result)
        : "Восстановление не выполнено.";
      publish({ list: snapshot.list, error: message });
      return result ?? { restored: false, code: "", message };
    }
    // The backend was restarted by the command: the list here is the one read
    // before the restore, and it stays until the next read.
    publish({ list: snapshot.list, error: "" });
    return result;
  } catch (error) {
    return report(error, "Не удалось выполнить восстановление резервной копии");
  }
}

/** The single owner of the backup read lifecycle for the Settings dialog and the
 *  panel. `api` is registered only when a caller has one (App builds it), so the
 *  props-free panel reuses that client instead of opening a second HTTP layer. */
export function useBackups(api?: Api | null): BackupSnapshot {
  useEffect(() => {
    if (api !== undefined) setBackupClient(api);
    if (client) void refreshBackups();
  }, [api]);
  return useSyncExternalStore(subscribeBackup, backupSnapshot, backupSnapshot);
}
