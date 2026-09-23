import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import BackupPanel from "../components/BackupPanel";
import SettingsDialog from "../components/SettingsDialog";
import type { Settings } from "../types";
import { Api } from "./api";
import {
  backupSnapshot,
  backupStatusLabel,
  createBackup,
  formatBytes,
  formatDate,
  kindLabel,
  plural,
  refreshBackups,
  restoreBackup,
  setBackupClient,
  verifyBackup,
  type BackupList,
  type BackupSummary,
  type BackupVerification,
} from "./backup";

const invokeMock = vi.hoisted(() => vi.fn());
vi.mock("@tauri-apps/api/core", () => ({ invoke: invokeMock }));

// The installed app owns the restore; the node test environment has no window.
const tauri = vi.hoisted(() => ({ enabled: true }));
vi.mock("./backend", () => ({ isTauriRuntime: () => tauri.enabled }));

const api = new Api("http://127.0.0.1:8000", "token");

const settings: Settings = {
  backendUrl: "http://127.0.0.1:8000",
  fontSize: 15,
  theme: "dark",
  language: "ru",
  enterSends: true,
  autoScroll: true,
  timestamps: true,
  technicalDetails: false,
  autoCheckUpdates: true,
  launchAtLogin: true,
};

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function summary(extra: Partial<BackupSummary> = {}): BackupSummary {
  return {
    id: "manual-20260615-093000",
    path: "C:\\Alex LLM\\backups\\manual-20260615-093000",
    kind: "manual",
    label: "",
    created_at: "2026-06-15T09:30:00+00:00",
    app_version: "0.9.3",
    schema_revision: "0015",
    files: 3,
    bytes: 2621440,
    verified: true,
    verified_at: "2026-06-15T09:30:05+00:00",
    complete: true,
    problem: null,
    ...extra,
  };
}

function verification(
  extra: Partial<BackupVerification> = {},
): BackupVerification {
  return {
    id: "manual-20260615-093000",
    path: "C:\\Alex LLM\\backups\\manual-20260615-093000",
    verified: true,
    verified_at: "2026-06-15T09:30:05+00:00",
    backup_format_version: 1,
    app_version: "0.9.3",
    schema_revision: "0015",
    created_at: "2026-06-15T09:30:00+00:00",
    files: 3,
    bytes: 2621440,
    documents: 2,
    kind: "manual",
    label: "",
    ...extra,
  };
}

function backupList(extra: Partial<BackupList> = {}): BackupList {
  return {
    state: {
      busy: "",
      last_result: null,
      backups_root: "C:\\Alex LLM\\backups",
      format_version: 1,
    },
    backups: [summary()],
    last_restore: null,
    keep: { automatic: 3, manual: 5 },
    ...extra,
  };
}

/** One authenticated read, as the panel does when its tab is opened. */
async function loadBackups(list: BackupList, extra: Partial<BackupList> = {}) {
  const fetchMock = vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValue(jsonResponse(200, { ...list, ...extra }));
  setBackupClient(api);
  await refreshBackups();
  return fetchMock;
}

/** JSX text keeps its source line breaks, so assertions run on flat markup. */
function flat(html: string): string {
  return html.replace(/\s+/g, " ");
}

function renderPanel(): string {
  return flat(renderToStaticMarkup(<BackupPanel />));
}

beforeEach(() => {
  invokeMock.mockReset();
  tauri.enabled = true;
  // A new session must not inherit the previous snapshot.
  setBackupClient(null);
});

afterEach(() => vi.restoreAllMocks());

describe("backup labels", () => {
  it("names every kind and status in Russian and invents none", () => {
    expect(kindLabel("manual")).toBe("Ручная");
    expect(kindLabel("pre_upgrade")).toBe("Перед обновлением");
    expect(kindLabel("pre_restore")).toBe("Перед восстановлением");
    expect(kindLabel(null)).toBe("Неизвестно");
    expect(backupStatusLabel(summary())).toBe("Проверена");
    expect(backupStatusLabel(summary({ verified: false }))).toBe(
      "Не проверена",
    );
    expect(
      backupStatusLabel(summary({ complete: false, verified: false })),
    ).toBe("Повреждена");
    // A damaged copy is never called verified, whatever the marker says.
    expect(backupStatusLabel(summary({ complete: false }))).toBe("Повреждена");
  });

  it("sizes bytes for a human", () => {
    expect(formatBytes(0)).toBe("0 Б");
    expect(formatBytes(1024)).toBe("1 КБ");
    expect(formatBytes(1536)).toBe("1,5 КБ");
    expect(formatBytes(2621440)).toBe("2,5 МБ");
    expect(formatBytes(Number.NaN)).toBe("размер неизвестен");
  });

  it("counts in Russian", () => {
    const label = (count: number) => plural(count, "копия", "копии", "копий");
    expect([1, 2, 5, 11, 21].map(label)).toEqual([
      "копия",
      "копии",
      "копий",
      "копий",
      "копия",
    ]);
  });
});

describe("backup panel", () => {
  it("renders the heading, the create button and no count before a read", () => {
    const html = renderPanel();
    expect(html).toContain("Резервные копии");
    expect(html).toContain("проверяем…");
    expect(html).toContain("Создать копию");
    expect(html).toContain("хранилище Windows");
  });

  it("says a session is needed instead of showing an empty list", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    await refreshBackups();
    const html = renderPanel();
    expect(html).toContain("Резервные копии");
    expect(html).toContain("недоступно");
    expect(html).toContain("доступны после входа в аккаунт");
    expect(html).toContain("Создать копию");
    expect(html).not.toContain("Резервных копий пока нет");
    // Without a session no authenticated request is made at all.
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("lists a verified backup with its date, kind, size and folder", async () => {
    const item = summary();
    await loadBackups(backupList({ backups: [item] }));
    const html = renderPanel();
    expect(html).toContain("1 копия");
    expect(html).toContain(flat(formatDate(item.created_at)));
    expect(html).toContain("Ручная");
    expect(html).toContain("версия 0.9.3");
    expect(html).toContain("схема 0015");
    expect(html).toContain(formatBytes(item.bytes));
    expect(html).toContain("3 файла");
    expect(html).toContain("Проверена");
    expect(html).not.toContain("Не проверена");
    expect(html).toContain("Папка резервных копий: C:\\Alex LLM\\backups");
    expect(html).toContain("Проверить");
    expect(html).toContain("Восстановить");
    // The path of one copy is never rendered.
    expect(html).not.toContain(item.path);
  });

  it("keeps an unverified copy and a damaged copy apart", async () => {
    await loadBackups(
      backupList({
        backups: [
          summary({
            id: "pre-upgrade-20260614-090000",
            kind: "pre_upgrade",
            verified: false,
            verified_at: null,
          }),
          summary({
            id: "manual-broken",
            kind: null,
            created_at: null,
            app_version: null,
            schema_revision: null,
            files: 0,
            bytes: 0,
            verified: false,
            verified_at: null,
            complete: false,
            problem: "backup_incomplete",
          }),
        ],
      }),
    );
    const html = renderPanel();
    expect(html).toContain("2 копии");
    expect(html).toContain("Перед обновлением");
    expect(html).toContain("Не проверена");
    expect(html).toContain("Повреждена");
    expect(html).toContain("дата неизвестна");
    expect(html).toContain("Неизвестно");
    // A raw backend code is not a message for a user.
    expect(html).not.toContain("backup_incomplete");
    // Only the intact copy can be verified or restored.
    expect(html).toMatch(/<button[^>]*disabled[^>]*>Проверить<\/button>/);
    expect(html).toMatch(/<button[^>]*disabled[^>]*>Восстановить<\/button>/);
  });

  it("shows the last restore the backend recorded", async () => {
    await loadBackups(
      backupList({
        last_restore: {
          restored: true,
          backup_id: "manual-20260615-093000",
          safety_backup_id: "pre-restore-20260616-080000",
          at: "2026-06-16T08:00:00+00:00",
        },
      }),
    );
    const html = renderPanel();
    expect(html).toContain("Последнее восстановление: выполнено");
    expect(html).toContain("manual-20260615-093000");
    expect(html).toContain("pre-restore-20260616-080000");
  });

  it("offers no restore outside the installed app", async () => {
    tauri.enabled = false;
    await loadBackups(backupList());
    const html = renderPanel();
    expect(html).toContain("Проверить");
    expect(html).not.toContain("Восстановить");
    expect(html).toContain("установленном приложении Canalla LLM");
  });

  it("does not offer a second operation while the backend is busy", async () => {
    await loadBackups(
      backupList({
        state: {
          busy: "manual",
          last_result: verification(),
          backups_root: "C:\\Alex LLM\\backups",
          format_version: 1,
        },
      }),
    );
    const html = renderPanel();
    expect(html).toMatch(
      /<button[^>]*disabled[^>]*>Операция выполняется…<\/button>/,
    );
    expect(html).not.toMatch(/<button[^>]*>Создать копию<\/button>/);
    expect(html).toMatch(/<button[^>]*disabled[^>]*>Проверить<\/button>/);
  });

  it("never renders a stored path or a credential-looking value", async () => {
    await loadBackups(
      backupList({
        backups: [
          summary({
            id: "jwt-manual-1",
            path: "C:\\data\\installation_secret\\manual-1",
            label: "Bearer secret-label",
            kind: null,
            verified: false,
            verified_at: null,
            complete: false,
            problem: "runpod_key_missing",
          }),
        ],
      }),
    );
    const html = renderPanel().toLowerCase();
    for (const secret of ["installation_secret", "bearer ", "runpod", "jwt"]) {
      expect(html).not.toContain(secret);
    }
    expect(html).not.toContain("secret-label");
  });
});

describe("backup operations", () => {
  it("creates a backup with the label and republishes the list", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse(200, backupList({ backups: [] })))
      .mockResolvedValueOnce(jsonResponse(200, verification()))
      .mockResolvedValueOnce(jsonResponse(200, backupList()));
    setBackupClient(api);
    await refreshBackups();
    expect(renderPanel()).toContain("Резервных копий пока нет");

    const result = await createBackup("Перед обновлением");
    const posts = fetchMock.mock.calls.filter(
      ([, init]) => init?.method === "POST",
    );
    expect(posts).toHaveLength(1);
    expect(posts[0][0]).toBe("http://127.0.0.1:8000/backup");
    expect(posts[0][1]?.body).toBe(
      JSON.stringify({ label: "Перед обновлением" }),
    );
    expect(result.verified).toBe(true);
    expect(backupSnapshot().list?.backups.map((item) => item.id)).toEqual([
      "manual-20260615-093000",
    ]);
    expect(renderPanel()).toContain("1 копия");
  });

  it("verifies one backup by id and the badge follows the answer", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        jsonResponse(
          200,
          backupList({
            backups: [summary({ verified: false, verified_at: null })],
          }),
        ),
      )
      .mockResolvedValueOnce(jsonResponse(200, verification()))
      .mockResolvedValueOnce(jsonResponse(200, backupList()));
    setBackupClient(api);
    await refreshBackups();
    expect(renderPanel()).toContain("Не проверена");

    await verifyBackup("manual-20260615-093000");
    const [url, init] = fetchMock.mock.calls[1];
    expect(url).toBe("http://127.0.0.1:8000/backup/verify");
    expect(init?.body).toBe(
      JSON.stringify({ backup_id: "manual-20260615-093000" }),
    );
    const html = renderPanel();
    expect(html).toContain("Проверена");
    expect(html).not.toContain("Не проверена");
  });

  it("asks for confirmation, then restores once with the id", async () => {
    const fetchMock = await loadBackups(backupList());
    const html = renderPanel();
    expect(html).toContain("Восстановить");
    expect(html).not.toContain("Да, восстановить");
    // The destructive step is its own state: it is not on screen before a click.
    expect(html).not.toContain("Текущие данные будут заменены");
    // Rendering the row is not a restore: nothing called the Desktop command.
    expect(invokeMock).not.toHaveBeenCalled();

    invokeMock.mockResolvedValue({
      restored: true,
      code: "restored",
      message: "Canalla восстановлен.",
      backup_id: "manual-20260615-093000",
      safety_backup_id: "pre-restore-20260616-080000",
    });
    const result = await restoreBackup("manual-20260615-093000");
    expect(invokeMock).toHaveBeenCalledTimes(1);
    expect(invokeMock).toHaveBeenCalledWith("restore_backup", {
      backupId: "manual-20260615-093000",
    });
    expect(result.restored).toBe(true);
    // Restore is never an HTTP request.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("reports a non-throwing restored: false as a failure and keeps the list", async () => {
    await loadBackups(backupList());
    invokeMock.mockResolvedValue({
      restored: false,
      code: "backend_stop_timeout",
      message:
        "Локальный сервер не остановился вовремя. Закройте Alex и повторите.",
    });
    const result = await restoreBackup("manual-20260615-093000");
    expect(result.restored).toBe(false);
    const html = renderPanel();
    expect(html).toContain("Локальный сервер не остановился вовремя.");
    expect(html).not.toContain("Canalla восстановлен из копии");
    expect(html).toContain("1 копия");
  });

  it("surfaces a 409 and keeps the previous list", async () => {
    const detail = "Другая операция с резервными копиями уже выполняется.";
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(jsonResponse(200, backupList()))
      .mockResolvedValueOnce(jsonResponse(409, { detail }));
    setBackupClient(api);
    await refreshBackups();
    await expect(createBackup("")).rejects.toThrow(detail);
    expect(backupSnapshot().list?.backups).toHaveLength(1);
    const html = renderPanel();
    expect(html).toContain(detail);
    expect(html).toContain("1 копия");
    // A refused create is not followed by a read that pretends otherwise.
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("reports a missing session instead of throwing at the caller", async () => {
    await expect(createBackup("")).rejects.toThrow(
      "доступны после входа в аккаунт",
    );
    await expect(verifyBackup("manual-1")).rejects.toThrow(
      "доступны после входа в аккаунт",
    );
    expect(renderPanel()).toContain("доступны после входа в аккаунт");
  });
});

describe("settings integration", () => {
  it("lists the backups tab next to Canalla Cloud", () => {
    const html = flat(
      renderToStaticMarkup(
        <SettingsDialog
          value={settings}
          onSave={() => {}}
          onClose={() => {}}
          api={api}
        />,
      ),
    );
    expect(html).toContain(">Резервные копии<");
    expect(html.indexOf(">Canalla Cloud<")).toBeLessThan(
      html.indexOf(">Резервные копии<"),
    );
    expect(html.indexOf(">Память<")).toBeLessThan(
      html.indexOf(">Резервные копии<"),
    );
  });
});
