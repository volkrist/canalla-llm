import { useEffect, useRef, useState, type FormEvent } from "react";
import { X } from "lucide-react";
import type { Settings } from "../types";
import { validateBackendUrl } from "../lib/api";
import type { UpdateStore } from "../hooks/useUpdates";
import type { Api } from "../lib/api";
import { exportChats } from "../lib/files";
import { clearDrafts, draftPrefix } from "../lib/drafts";
import { recoveryPlan } from "../lib/errors";
import { ACTION_TEXT } from "../lib/status";
import { requestDeviceRefresh, useDeviceStatus } from "../lib/device";
import type { DeviceStatus } from "../lib/host";
import { useBusy } from "../lib/busy";
import UpdatesPanel from "./UpdatesPanel";
import WebToolsSettings from "./WebToolsSettings";
import ProviderSecretPanel from "./ProviderSecretPanel";
import AlexCloudPanel from "./AlexCloudPanel";
import BackupPanel from "./BackupPanel";
import EmbeddingModelStatus from "./EmbeddingModelStatus";
import PersonalPanel from "./PersonalPanel";
import { isSharedMode, useCloud } from "../lib/cloud";
import { useBackups } from "../lib/backup";
import {
  autostartError,
  readAutostart,
  writeAutostart,
  UNSUPPORTED,
  type AutostartStatus,
} from "../lib/autostart";
import { version } from "../../package.json";

/** The single navigation for account, workspace and device settings. */
export const SETTINGS_GROUPS = [
  { title: "Общие", tabs: ["Общие", "Чат", "Профиль"] },
  { title: "Личное", tabs: ["Пользователи", "Проекты", "Память"] },
  {
    title: "AI и инструменты",
    tabs: ["AI / Compute", "Canalla Cloud", "Computer", "Web / Tor"],
  },
  { title: "Данные", tabs: ["Резервные копии", "Данные"] },
  { title: "Система", tabs: ["Дополнительно", "Обновления", "О программе"] },
] as const;

const TABS = SETTINGS_GROUPS.flatMap((group) => group.tabs);

type Tab = (typeof TABS)[number];
type PersonalTab = "Профиль" | "Пользователи" | "Проекты" | "Память";

/** The personal sections render inside this dialog, in the Settings shell itself. */
const PERSONAL_TABS: readonly PersonalTab[] = [
  "Профиль",
  "Пользователи",
  "Проекты",
  "Память",
];

function personalTab(tab: Tab): PersonalTab | null {
  return (PERSONAL_TABS as readonly string[]).includes(tab)
    ? (tab as PersonalTab)
    : null;
}

/** The local device snapshot, phrased as a state instead of a promise. */
function deviceLine(state: DeviceStatus): string {
  const name = state.display_name ? ` · ${state.display_name}` : "";
  if (!state.paired) return `Устройство: не сопряжено${name}`;
  return `Устройство: ${state.online ? "сопряжено и отвечает" : "сопряжено, но не отвечает"}${name}`;
}

export default function SettingsDialog({
  value,
  onSave,
  onClose,
  api,
  userId,
  onLogout = () => {},
  updates = null,
}: {
  value: Settings;
  onSave: (settings: Settings) => void;
  onClose: () => void;
  api?: Api;
  userId?: string;
  /** The personal sections render here and reuse the workspace session handler. */
  onLogout?: () => void;
  /** The app's single update store. */
  updates?: UpdateStore | null;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [url, setUrl] = useState(value.backendUrl);
  const [fontSize, setFontSize] = useState(value.fontSize);
  const [error, setError] = useState("");
  const [section, setSection] = useState<Tab>("Общие");
  const [options, setOptions] = useState(value);
  const [clear, setClear] = useState(false);
  // The same snapshot the header chip and the compact Computer bar show: one reader, no drift.
  const device = useDeviceStatus();
  // The connect action reuses the device loop the Computer chip already uses.
  const connect = recoveryPlan("reconnect");
  const person = personalTab(section);
  // Anything the user is waiting for (a generation, a task, a backup, a restore) defers an
  // update install: the installer must never restart the app in the middle of it.
  const busy = useBusy();
  // The cloud state decides whether a RunPod key is still needed at all. While it
  // is unknown the local key path stays available.
  const cloud = useCloud(api ?? null);
  // «Запускать Canalla вместе с Windows»: the machine's answer, not the stored setting.
  const [autostart, setAutostart] = useState<AutostartStatus>(UNSUPPORTED);
  const [autostartBusy, setAutostartBusy] = useState(false);
  const [autostartMessage, setAutostartMessage] = useState("");
  const sharedGateway = isSharedMode(cloud.status);
  // The backup panel is props-free like the cloud panel, so the authenticated
  // client is registered here and the panel reuses it.
  useBackups(api ?? null);
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  useEffect(() => {
    window.addEventListener("alex-browser-advanced", onClose);
    return () => window.removeEventListener("alex-browser-advanced", onClose);
  }, [onClose]);
  useEffect(() => {
    // The registry is the source of truth: the toggle shows what Windows has, not what we stored.
    void readAutostart().then(setAutostart);
  }, []);
  async function toggleAutostart(enabled: boolean) {
    setAutostartBusy(true);
    setAutostartMessage("");
    const result = await writeAutostart(enabled);
    setAutostart(result.status);
    setAutostartMessage(result.error ? autostartError(result.error) : "");
    // The stored policy follows a successful change only: a refused write must not look chosen.
    if (!result.error) setOptions({ ...options, launchAtLogin: enabled });
    setAutostartBusy(false);
  }
  function connectDevice() {
    if (connect.event) window.dispatchEvent(new Event(connect.event));
    // Ask the one device loop for a fresh read: a user action, not a second poller.
    requestDeviceRefresh();
  }
  function save(event: FormEvent) {
    event.preventDefault();
    try {
      onSave({ ...options, backendUrl: validateBackendUrl(url), fontSize });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Некорректный URL");
    }
  }
  return (
    <dialog ref={dialog} onCancel={onClose} className="settings-dialog">
      <div className="dialog-title">
        <h2>Settings</h2>
        <button
          className="icon-button"
          type="button"
          aria-label="Закрыть настройки"
          onClick={onClose}
        >
          <X size={20} />
        </button>
      </div>
      <p className="muted">Настройте своё рабочее пространство.</p>
      <div className="settings-nav">
        {SETTINGS_GROUPS.map((group) => (
          <nav
            className="settings-group"
            key={group.title}
            aria-label={`Разделы настроек: ${group.title}`}
          >
            <span className="settings-group-title">{group.title}</span>
            <div className="settings-tabs">
              {group.tabs.map((tab) => (
                <button
                  type="button"
                  aria-pressed={section === tab}
                  key={tab}
                  onClick={() => setSection(tab)}
                >
                  {tab}
                </button>
              ))}
            </div>
          </nav>
        ))}
      </div>
      {person ? (
        // The personal sections are part of this dialog, not a panel over it: same shell, same
        // session, no second entry point to keep in sync. The form below is skipped so their own
        // forms are never nested inside the settings form.
        api ? (
          <PersonalPanel
            api={api}
            onLogout={onLogout}
            embedded
            section={person}
          />
        ) : (
          <p>Войдите в аккаунт.</p>
        )
      ) : (
        <form onSubmit={save}>
          {section === "О программе" && <p>Canalla LLM {version}</p>}
          {section === "Web / Tor" &&
            (api ? <WebToolsSettings api={api} /> : <p>Войдите в аккаунт.</p>)}
          {section === "Canalla Cloud" && <AlexCloudPanel />}
          {section === "Резервные копии" && <BackupPanel />}
          {section === "Computer" && (
            <>
              <p>{deviceLine(device)}</p>
              <button type="button" onClick={connectDevice}>
                {ACTION_TEXT.reconnect}
              </button>
              <p className="field-help">
                Сопряжение выполняет приложение на этом компьютере; кнопка
                запускает повторную попытку подключения.
              </p>
            </>
          )}
          {section === "AI / Compute" && (
            <>
              <p>
                Выбор GPU, лимит цены, бюджет сессии и автоостановка задаются в
                управлении compute.
              </p>
              {sharedGateway ? (
                <p className="muted">
                  Ключ RunPod здесь не нужен: используется общий Gateway Canalla
                  Cloud.
                </p>
              ) : (
                <ProviderSecretPanel />
              )}
              <button
                type="button"
                disabled={!api}
                onClick={() => {
                  onClose();
                  window.dispatchEvent(new Event("alex-open-compute"));
                }}
              >
                Открыть управление compute
              </button>
              {!api && <p>Войдите в аккаунт для настройки compute.</p>}
            </>
          )}
          {section === "Дополнительно" && (
            <>
              <label>
                Backend URL
                <input
                  type="url"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  required
                />
              </label>
              <p className="field-help">
                Адрес вашего сервера. При смене адреса потребуется войти заново.
                Для удалённого сервера используйте HTTPS.
              </p>
              <label>
                <input
                  type="checkbox"
                  checked={options.technicalDetails}
                  onChange={(e) =>
                    setOptions({
                      ...options,
                      technicalDetails: e.target.checked,
                    })
                  }
                />
                Показывать технические сведения
              </label>
            </>
          )}
          {section === "Обновления" &&
            (updates ? (
              <UpdatesPanel
                autoCheck={options.autoCheckUpdates}
                onAutoCheck={(value) =>
                  setOptions({ ...options, autoCheckUpdates: value })
                }
                busy={busy}
                updates={updates}
              />
            ) : (
              <p className="muted">
                Обновления доступны в установленном приложении.
              </p>
            ))}
          {section === "Общие" && (
            <>
              <label>
                Размер текста
                <select
                  value={fontSize}
                  onChange={(e) => setFontSize(Number(e.target.value))}
                >
                  <option value={14}>Компактный</option>
                  <option value={15}>Обычный</option>
                  <option value={17}>Крупный</option>
                  <option value={19}>Очень крупный</option>
                </select>
              </label>
              <label>
                Тема
                <select
                  value={options.theme}
                  onChange={(e) =>
                    setOptions({
                      ...options,
                      theme: e.target.value as Settings["theme"],
                    })
                  }
                >
                  <option value="dark">Тёмная</option>
                  <option value="system">Системная</option>
                </select>
              </label>
              <label>
                Язык интерфейса
                <select value="ru" disabled>
                  <option value="ru">Русский</option>
                </select>
              </label>
              <label className="settings-toggle">
                <input
                  type="checkbox"
                  checked={autostart.enabled}
                  disabled={!autostart.supported || autostartBusy}
                  data-testid="autostart-toggle"
                  onChange={(event) =>
                    void toggleAutostart(event.target.checked)
                  }
                />
                <span>Запускать Canalla вместе с Windows</span>
              </label>
              <p className="field-help">
                {autostart.supported
                  ? "Состояние читается из Windows: при включении Canalla запускается при входе в систему, при выключении запись удаляется."
                  : "Автозапуск доступен в установленном приложении."}
              </p>
              {autostart.error || autostartMessage ? (
                <p
                  className="settings-note"
                  role="alert"
                  data-testid="autostart-error"
                >
                  {autostartMessage || autostartError(autostart.error)}
                </p>
              ) : null}
            </>
          )}
          {section === "Чат" && (
            <>
              {(
                [
                  ["enterSends", "Enter отправляет сообщение"],
                  ["autoScroll", "Прокручивать новые ответы, если вы внизу"],
                  ["timestamps", "Время сообщений"],
                ] as const
              ).map(([key, label]) => (
                <label key={key}>
                  <input
                    type="checkbox"
                    checked={options[key]}
                    onChange={(e) =>
                      setOptions({ ...options, [key]: e.target.checked })
                    }
                  />
                  {label}
                </label>
              ))}
              <p>
                Ctrl + Enter всегда отправляет. Shift + Enter добавляет строку.
              </p>
            </>
          )}
          {section === "Данные" && (
            <>
              <p>
                Экспорт содержит только ваши диалоги. Черновики хранятся на этом
                устройстве отдельно для каждого аккаунта и backend.
              </p>
              {api && (
                <>
                  {(["markdown", "json"] as const).map((format) => (
                    <button
                      key={format}
                      type="button"
                      onClick={() =>
                        void exportChats(api, format).catch((e: Error) =>
                          setError(e.message),
                        )
                      }
                    >
                      Экспорт всех чатов · {format}
                    </button>
                  ))}
                  <button type="button" onClick={() => setClear(true)}>
                    Очистить локальные черновики
                  </button>
                  <label>Поиск по файлам</label>
                  <EmbeddingModelStatus api={api} />
                  <p className="field-help">
                    Работает на этом сервере: поиск по вашим документам без
                    обращения к внешним сервисам.
                  </p>
                  {clear && (
                    <div>
                      <p>Удалить черновики этого аккаунта на устройстве?</p>
                      <button
                        type="button"
                        onClick={() => {
                          if (userId)
                            clearDrafts(draftPrefix(api.base, userId));
                          window.dispatchEvent(
                            new Event("alex-drafts-cleared"),
                          );
                          setClear(false);
                        }}
                      >
                        Подтвердить очистку
                      </button>
                      <button type="button" onClick={() => setClear(false)}>
                        Отмена
                      </button>
                    </div>
                  )}
                </>
              )}
            </>
          )}
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <button className="primary" type="submit">
            Сохранить настройки
          </button>
        </form>
      )}
    </dialog>
  );
}
