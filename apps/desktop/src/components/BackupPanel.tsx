import { useState } from "react";
import { isTauriRuntime } from "../lib/backend";
import { clearBusy, markBusy } from "../lib/busy";
import {
  backupStatusLabel,
  createBackup,
  formatBytes,
  formatDate,
  kindLabel,
  plural,
  restoreBackup,
  useBackups,
  verifyBackup,
  type BackupRestoreRecord,
  type RestoreResult,
} from "../lib/backup";

/** Only what the backend recorded about the last restore. */
function lastRestoreText(record: BackupRestoreRecord): string {
  const parts = [record.restored === false ? "не выполнено" : "выполнено"];
  if (record.at) parts.push(formatDate(record.at));
  if (record.backup_id) parts.push(`копия ${record.backup_id}`);
  if (record.safety_backup_id) {
    parts.push(`копия перед восстановлением ${record.safety_backup_id}`);
  }
  if (record.restored === false && record.message) parts.push(record.message);
  return parts.join(" · ");
}

/** The restored copy and the safety copy Canalla kept are both named. The list on screen
 *  was read before the restore, so the message says when it refreshes. */
function restoreMessage(result: RestoreResult, fallbackId: string): string {
  const safety = result.safety_backup_id
    ? ` Перед восстановлением сохранена копия ${result.safety_backup_id}.`
    : "";
  const hints = " Список обновится при следующем открытии этого раздела.";
  return `Canalla восстановлен из копии ${result.backup_id || fallbackId}.${safety}${hints}`;
}

/** Резервные копии: список, проверка и восстановление. No path of an individual
 *  copy is shown, and a restore always needs an explicit confirmation here: the
 *  Desktop command stops the backend and replaces the live data. */
export default function BackupPanel() {
  const tauri = isTauriRuntime();
  const { list, error } = useBackups();
  const [pending, setPending] = useState("");
  const [confirm, setConfirm] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const serverBusy = Boolean(list?.state.busy);
  const busy = serverBusy || pending !== "";
  const count = list ? list.backups.length : 0;
  const createLabel =
    pending === "create"
      ? "Создаём копию…"
      : serverBusy
        ? "Операция выполняется…"
        : "Создать копию";

  /** A failure is recorded by the module, so the panel renders it from the state
   *  and never shows the same sentence twice. */
  async function act(action: string, task: () => Promise<void>) {
    // A backup, a verification or a restore is work the user waits for: an update install must not
    // restart the app in the middle of it.
    const kind = action.startsWith("restore") ? "restore" : "backup";
    setPending(action);
    markBusy(kind);
    setMessage("");
    try {
      await task();
    } catch {
      // The module already published the reason.
    } finally {
      setPending("");
      clearBusy(kind);
    }
  }

  function create() {
    void act("create", async () => {
      const result = await createBackup("");
      setMessage(
        `Копия создана и проверена: ${formatDate(result.created_at)} · ` +
          `${formatBytes(result.bytes)} · ${result.files} ` +
          `${plural(result.files, "файл", "файла", "файлов")}.`,
      );
    });
  }

  function verify(id: string) {
    void act(`verify:${id}`, async () => {
      await verifyBackup(id);
    });
  }

  function restore(id: string) {
    setConfirm(null);
    void act(`restore:${id}`, async () => {
      const result = await restoreBackup(id);
      if (result.restored) setMessage(restoreMessage(result, id));
    });
  }

  return (
    <div>
      <p role="status">
        <strong>Резервные копии</strong>
        {list
          ? ` · ${count} ${plural(count, "копия", "копии", "копий")}`
          : ` · ${error ? "недоступно" : "проверяем…"}`}
      </p>
      <p className="field-help">
        Копия содержит локальную базу (чаты, сообщения, проекты, память,
        настройки) и документы. Пароли, ключ облачного GPU-провайдера и данные
        установки Canalla Cloud в копию не входят: они остаются в защищённом
        хранилище Windows.
      </p>
      {error && <p className="muted">{error}</p>}
      <button
        className="primary"
        type="button"
        disabled={busy}
        onClick={create}
      >
        {createLabel}
      </button>
      {message && <p className="muted">{message}</p>}
      {list && count === 0 && (
        <p className="muted">Резервных копий пока нет.</p>
      )}
      {list?.backups.map((item) => (
        <div className="document-row" key={item.id}>
          <p>
            <strong>{formatDate(item.created_at)}</strong> ·{" "}
            {kindLabel(item.kind)}
          </p>
          <p className="muted">
            {item.app_version
              ? `версия ${item.app_version}`
              : "версия неизвестна"}{" "}
            · схема {item.schema_revision || "неизвестна"} ·{" "}
            {formatBytes(item.bytes)} · {item.files}{" "}
            {plural(item.files, "файл", "файла", "файлов")}
          </p>
          <p className="muted">
            <span className="eyebrow">{backupStatusLabel(item)}</span>
          </p>
          {!item.complete && (
            <p className="field-help">
              Копия неполная или повреждена; проверка и восстановление
              недоступны.
            </p>
          )}
          {confirm === item.id ? (
            <div>
              <p className="muted">
                Восстановить Canalla из этой копии? Текущие данные будут
                заменены; перед заменой Canalla сохранит копию текущего
                состояния.
              </p>
              <button
                type="button"
                disabled={busy}
                onClick={() => restore(item.id)}
              >
                Да, восстановить
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => setConfirm(null)}
              >
                Отмена
              </button>
            </div>
          ) : (
            <div>
              <button
                type="button"
                disabled={busy || !item.complete}
                onClick={() => verify(item.id)}
              >
                {pending === `verify:${item.id}` ? "Проверяем…" : "Проверить"}
              </button>
              {tauri && (
                <button
                  type="button"
                  disabled={busy || !item.complete}
                  onClick={() => setConfirm(item.id)}
                >
                  Восстановить
                </button>
              )}
            </div>
          )}
        </div>
      ))}
      {list && (
        <>
          <p className="muted">
            Папка резервных копий: {list.state.backups_root}
          </p>
          {list.last_restore && (
            <p className="muted">
              Последнее восстановление: {lastRestoreText(list.last_restore)}
            </p>
          )}
        </>
      )}
      {!tauri && (
        <p className="field-help">
          Восстановление доступно в установленном приложении Canalla LLM: оно
          останавливает локальный сервер и запускает его заново.
        </p>
      )}
    </div>
  );
}
