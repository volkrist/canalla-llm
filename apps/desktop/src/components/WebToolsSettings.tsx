import { useEffect, useState } from "react";
import type { Api } from "../lib/api";
import {
  deleteUserCredential,
  forgetLocalDevice,
  listUserCredentials,
  readDeviceStatus,
  rotateDeviceCredential,
  storeUserCredential,
  type DeviceStatus,
} from "../lib/host";
import type { ComputerMode, TorMode, WebMode } from "../lib/tools";

interface Preferences {
  search_enabled: boolean;
  fetch_enabled: boolean;
  default_mode: WebMode;
  agent_enabled: boolean;
  agent_run_budget: number;
  agent_daily_budget: number;
  agent_max_runtime: number;
  browser_enabled: boolean;
  tor_mode: TorMode;
  tor_enabled: boolean;
  computer_mode: ComputerMode;
  workspace_roots: string[];
  device_display_name: string;
}
interface Status {
  agent_read_only_enforced: boolean;
  configured: boolean;
  search_fetch_free: boolean;
  agent_step_price: number;
  browser_minute_price: number;
  agent_max_steps_supported: boolean;
  browser_delete_supported: boolean;
  tor_status: string;
  tor_search_configured: boolean;
  limits: Record<string, number>;
}
export default function WebToolsSettings({ api }: { api: Api }) {
  const [value, setValue] = useState<Preferences | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [device, setDevice] = useState<DeviceStatus>({
    paired: false,
    online: false,
  });
  const [credName, setCredName] = useState("");
  const [credSecret, setCredSecret] = useState("");
  const [credNames, setCredNames] = useState<string[]>([]);
  useEffect(() => {
    let live = true;
    void Promise.all([
      api.json<Preferences>("/tools/preferences"),
      api.json<Status>("/tools/status"),
      readDeviceStatus().catch(() => ({ paired: false, online: false })),
      listUserCredentials().catch(() => [] as string[]),
    ])
      .then(([prefs, state, host, names]) => {
        if (live) {
          setValue({
            search_enabled: prefs.search_enabled,
            fetch_enabled: prefs.fetch_enabled,
            default_mode: prefs.default_mode,
            agent_enabled: prefs.agent_enabled,
            agent_run_budget: prefs.agent_run_budget,
            agent_daily_budget: prefs.agent_daily_budget,
            agent_max_runtime: prefs.agent_max_runtime,
            browser_enabled: prefs.browser_enabled,
            tor_mode: prefs.tor_mode ?? (prefs.tor_enabled ? "auto" : "off"),
            tor_enabled:
              (prefs.tor_mode ?? (prefs.tor_enabled ? "auto" : "off")) !==
              "off",
            computer_mode: prefs.computer_mode ?? "ask",
            workspace_roots: prefs.workspace_roots ?? [],
            device_display_name: prefs.device_display_name ?? "",
          });
          setStatus(state);
          setDevice(host);
          setCredNames(names);
        }
      })
      .catch((e: Error) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, [api]);
  const change = (patch: Partial<Preferences>) => {
    if (value) setValue({ ...value, ...patch });
    setSaved(false);
  };
  async function save() {
    try {
      setValue(
        await api.json<Preferences>("/tools/preferences", {
          method: "PUT",
          body: JSON.stringify(value),
        }),
      );
      setError("");
      setSaved(true);
      window.dispatchEvent(new Event("alex-tools-settings"));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось сохранить");
    }
  }
  async function forget() {
    const token = api.authToken();
    if (!token) return;
    try {
      setDevice(await forgetLocalDevice(api.base, token));
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось забыть устройство");
    }
  }
  async function rotate() {
    const token = api.authToken();
    if (!token) return;
    try {
      setDevice(await rotateDeviceCredential(api.base, token));
      setError("");
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Не удалось сменить credential",
      );
    }
  }
  async function saveCredential() {
    if (!credName.trim() || !credSecret) return;
    try {
      await storeUserCredential(credName.trim(), credSecret);
      setCredSecret("");
      setCredNames(await listUserCredentials());
      setError("");
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Не удалось сохранить credential",
      );
    }
  }
  async function removeCredential(name: string) {
    try {
      await deleteUserCredential(name);
      setCredNames(await listUserCredentials());
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Не удалось удалить credential",
      );
    }
  }
  return (
    <section aria-label="Web & Tools">
      <h3>Web & Tools</h3>
      <p>
        TinyFish API:{" "}
        {status
          ? status.configured
            ? "Configured"
            : "Not configured"
          : "Проверка…"}
      </p>
      {status && !status.configured && (
        <p>
          Администратор настраивает TINYFISH_API_KEY в backend .env. Ключ в
          приложение не передаётся.
        </p>
      )}
      {value && (
        <>
          <fieldset>
            <legend>Web Search / Fetch</legend>
            <label>
              <input
                type="checkbox"
                checked={value.search_enabled}
                onChange={(e) => change({ search_enabled: e.target.checked })}
              />
              Разрешить web search
            </label>
            <label>
              <input
                type="checkbox"
                checked={value.fetch_enabled}
                onChange={(e) => change({ fetch_enabled: e.target.checked })}
              />
              Разрешить чтение страниц
            </label>
            <label>
              Web по умолчанию{" "}
              <select
                aria-label="Web по умолчанию"
                value={value.default_mode}
                onChange={(e) =>
                  change({ default_mode: e.target.value as WebMode })
                }
              >
                <option value="off">Off</option>
                <option value="auto">Auto</option>
                <option value="on">On</option>
              </select>
            </label>
            <p>
              {status?.search_fetch_free
                ? "Free under current TinyFish pricing"
                : "Тариф Search/Fetch требует проверки администратором"}
            </p>
          </fieldset>
          <fieldset>
            <legend>Web Agent · Paid</legend>
            {status && !status.agent_read_only_enforced && (
              <p role="status">
                Запуск Agent заблокирован: текущий API не гарантирует read-only
                и не даёт подтверждать каждый side effect. Адаптер подготовлен;
                доступны Search/Fetch и Browser Advanced.
              </p>
            )}
            <label>
              <input
                aria-label="Разрешить платный Agent"
                disabled={!status?.agent_read_only_enforced}
                type="checkbox"
                checked={value.agent_enabled}
                onChange={(e) => change({ agent_enabled: e.target.checked })}
              />
              Разрешить платный Agent только для чтения
            </label>
            <p>
              Оценка: {"$" + (status?.agent_step_price ?? "?")} за шаг. Записи,
              покупки и вход с паролем через Agent не поддерживаются.
            </p>
            <label>
              Бюджет запуска, $
              <input
                aria-label="Бюджет tool run"
                type="number"
                min="0.01"
                max="10"
                step="0.01"
                value={value.agent_run_budget}
                onChange={(e) =>
                  change({ agent_run_budget: Number(e.target.value) })
                }
              />
            </label>
            <label>
              Дневной бюджет Agent / Browser, $
              <input
                aria-label="Дневной бюджет tools"
                type="number"
                min="0.01"
                max="50"
                step="0.01"
                value={value.agent_daily_budget}
                onChange={(e) =>
                  change({ agent_daily_budget: Number(e.target.value) })
                }
              />
            </label>
            <label>
              Максимум времени, с
              <input
                aria-label="Время tool run"
                type="number"
                min="10"
                max="600"
                value={value.agent_max_runtime}
                onChange={(e) =>
                  change({ agent_max_runtime: Number(e.target.value) })
                }
              />
            </label>
            <p>
              {status?.agent_max_steps_supported
                ? "Provider max_steps включён для аккаунта."
                : "Бюджет soft/local: max_steps у провайдера не подтверждён. Возможен перерасход до подтверждения отмены."}
            </p>
          </fieldset>
          <fieldset>
            <legend>Direct Browser · Advanced · Paid</legend>
            <p>
              Отдельная платная сессия:{" "}
              {"$" + (status?.browser_minute_price ?? "?")}/мин. Автоматически
              для чата не запускается.
            </p>
            <p>
              {status?.browser_delete_supported
                ? "Stop отправляет завершение сессии провайдеру. Если подтверждение не получено, сессия может продолжать тарифицироваться до timeout."
                : "Провайдер не подтверждает явное удаление: Stop прекращает локальные команды; supplier может тарифицировать до часа простоя."}
            </p>
            <label>
              <input
                aria-label="Разрешить Browser Advanced"
                type="checkbox"
                checked={value.browser_enabled}
                onChange={(e) => change({ browser_enabled: e.target.checked })}
              />
              Понимаю платный lifecycle, включить Browser Advanced
            </label>
            {value.browser_enabled && (
              <button
                type="button"
                onClick={() =>
                  window.dispatchEvent(new Event("alex-browser-advanced"))
                }
              >
                Открыть Browser Advanced
              </button>
            )}
          </fieldset>
          <fieldset>
            <legend>Tor</legend>
            <p>
              Tor SOCKS: {status?.tor_status || "Проверка…"}
              {status && !status.tor_search_configured
                ? " · Tor Search provider not configured"
                : ""}
            </p>
            <label>
              Tor{" "}
              <select
                aria-label="Tor mode default"
                value={value.tor_mode}
                onChange={(e) => {
                  const tor_mode = e.target.value as TorMode;
                  change({ tor_mode, tor_enabled: tor_mode !== "off" });
                }}
              >
                <option value="off">Off</option>
                <option value="auto">Auto</option>
                <option value="on">On</option>
              </select>
            </label>
            <p>
              Off — без Tor. Auto — только при явном запросе Tor/onion. On — для
              research, но не для приветствий, правки текста, математики и
              локальных задач. Direct fallback нет. GUI Tor Browser не
              используется автоматически.
            </p>
          </fieldset>
          <fieldset>
            <legend>Local Computer</legend>
            <label>
              Computer{" "}
              <select
                aria-label="Computer mode default"
                value={value.computer_mode}
                onChange={(e) =>
                  change({ computer_mode: e.target.value as ComputerMode })
                }
              >
                <option value="off">Off</option>
                <option value="ask">Ask</option>
                <option value="trusted">Trusted Workspace</option>
              </select>
            </label>
            <label>
              Имя устройства
              <input
                aria-label="Device display name"
                value={value.device_display_name}
                maxLength={80}
                placeholder="Windows device"
                onChange={(e) =>
                  change({ device_display_name: e.target.value })
                }
              />
            </label>
            <label>
              Trusted workspace roots (по строке)
              <textarea
                aria-label="Workspace roots"
                rows={3}
                value={(value.workspace_roots || []).join("\n")}
                onChange={(e) =>
                  change({
                    workspace_roots: e.target.value
                      .split("\n")
                      .map((item) => item.trim())
                      .filter(Boolean)
                      .slice(0, 8),
                  })
                }
              />
            </label>
            <p>
              Alex работает со всем локальным компьютером. Trusted Workspace
              снижает подтверждения для безопасных NORMAL_CHANGE внутри roots
              (запись файлов, процессы с cwd в roots). READ доступен везде.
              SENSITIVE и CRITICAL всегда требуют объяснение и «Разрешить один
              раз». CRITICAL не имеет Always allow. Device credential хранится в
              Windows Credential Manager, JS его не читает.
            </p>
            <p>
              Устройство: {device.display_name || "Windows device"}
              {device.storage ? ` · ${device.storage}` : ""}
              {device.paired ? " · paired" : " · not paired"}
            </p>
            <button type="button" onClick={() => void forget()}>
              Forget this device
            </button>
            <button type="button" onClick={() => void rotate()}>
              Rotate device credential
            </button>
            <fieldset>
              <legend>Локальные credentials</legend>
              <p>
                LLM видит только ссылку, например github-main. Секрет остаётся
                на этом компьютере.
              </p>
              <label>
                Ссылка
                <input
                  aria-label="Credential reference"
                  value={credName}
                  maxLength={80}
                  onChange={(e) => setCredName(e.target.value)}
                />
              </label>
              <label>
                Секрет
                <input
                  aria-label="Credential secret"
                  type="password"
                  value={credSecret}
                  onChange={(e) => setCredSecret(e.target.value)}
                />
              </label>
              <button type="button" onClick={() => void saveCredential()}>
                Сохранить локально
              </button>
              <ul>
                {credNames.map((name) => (
                  <li key={name}>
                    {name}{" "}
                    <button
                      type="button"
                      onClick={() => void removeCredential(name)}
                    >
                      Удалить
                    </button>
                  </li>
                ))}
              </ul>
            </fieldset>
          </fieldset>
          {status && (
            <p>
              Лимиты backend: {status.limits.calls} вызовов ·{" "}
              {status.limits.pages} страниц · {status.limits.chars} символов ·{" "}
              {status.limits.seconds} с.
            </p>
          )}
          <button type="button" onClick={() => void save()}>
            Сохранить Web & Tools
          </button>
          {saved && <p role="status">Настройки Web & Tools сохранены</p>}
        </>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
