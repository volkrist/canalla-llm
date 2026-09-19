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
  agent_mode: WebMode;
  agent_enabled: boolean;
  agent_run_budget: number;
  agent_daily_budget: number;
  agent_max_runtime: number;
  agent_max_runs: number;
  agent_max_steps: number;
  browser_mode: WebMode;
  browser_enabled: boolean;
  tinyfish_paid_task_budget_usd: number;
  browser_max_sessions: number;
  browser_max_minutes: number;
  tor_mode: TorMode;
  tor_enabled: boolean;
  tor_browser_mode: TorMode;
  computer_mode: ComputerMode;
  workspace_roots: string[];
  device_display_name: string;
  auto_commit: boolean;
  allow_push: boolean;
}
interface Status {
  agent_read_only_enforced: boolean;
  agent_preaction_approval?: boolean;
  agent_side_effect_blocked_before_run?: boolean;
  configured: boolean;
  search_fetch_free: boolean;
  agent_step_price: number;
  browser_minute_price: number;
  agent_max_steps_supported: boolean;
  browser_delete_supported: boolean;
  task_limits?: {
    paid_budget: number;
    agent_steps: number;
    browser_minutes: number;
  };
  tor_status: string;
  tor_search_configured: boolean;
  tor_browser?: {
    installed?: boolean;
    detected?: boolean;
    automation?: string;
    version?: string | null;
  };
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
            agent_mode: prefs.agent_mode ?? "auto",
            agent_enabled: (prefs.agent_mode ?? "auto") !== "off",
            agent_run_budget: prefs.agent_run_budget,
            agent_daily_budget: prefs.agent_daily_budget,
            agent_max_runtime: prefs.agent_max_runtime,
            agent_max_runs: prefs.agent_max_runs ?? 2,
            agent_max_steps: prefs.agent_max_steps ?? 20,
            browser_mode: prefs.browser_mode ?? "auto",
            browser_enabled: (prefs.browser_mode ?? "auto") !== "off",
            tinyfish_paid_task_budget_usd:
              prefs.tinyfish_paid_task_budget_usd ?? 1,
            browser_max_sessions: prefs.browser_max_sessions ?? 2,
            browser_max_minutes: prefs.browser_max_minutes ?? 10,
            tor_mode: prefs.tor_mode ?? (prefs.tor_enabled ? "auto" : "off"),
            tor_enabled:
              (prefs.tor_mode ?? (prefs.tor_enabled ? "auto" : "off")) !==
              "off",
            tor_browser_mode: prefs.tor_browser_mode ?? "auto",
            computer_mode: prefs.computer_mode ?? "ask",
            workspace_roots: prefs.workspace_roots ?? [],
            device_display_name: prefs.device_display_name ?? "",
            auto_commit: prefs.auto_commit ?? false,
            allow_push: prefs.allow_push ?? false,
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
              Search: {status?.configured ? "Available" : "Not configured"}
              {status?.search_fetch_free ? " / Free" : ""}
            </p>
            <p>
              Fetch: {status?.configured ? "Available" : "Not configured"}
              {status?.search_fetch_free ? " / Free" : ""}
            </p>
          </fieldset>
          <fieldset>
            <legend>TinyFish Agent · Paid</legend>
            <p>
              Agent: {status?.configured ? "Configured" : "Not configured"} /
              Paid. Off — planner не видит tool. Auto — только если router
              считает задачу сложным read-only web workflow. On —
              предпочтительно для таких задач, не для приветствий, математики,
              local-only и Tor.
            </p>
            <p>
              Текущий TinyFish Agent API не даёт enforceable pre-action
              approval. Side-effect цели блокируются до запуска. Prompt не
              является security boundary.
            </p>
            <label>
              Agent{" "}
              <select
                aria-label="TinyFish Agent mode"
                value={value.agent_mode}
                onChange={(e) => {
                  const agent_mode = e.target.value as WebMode;
                  change({ agent_mode, agent_enabled: agent_mode !== "off" });
                }}
              >
                <option value="off">Off</option>
                <option value="auto">Auto</option>
                <option value="on">On</option>
              </select>
            </label>
            <p>
              Оценка: {"$" + (status?.agent_step_price ?? "?")} за шаг. Vault и
              Browser Context Profiles по умолчанию выключены.
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
            <label>
              Максимум Agent runs / task
              <input
                aria-label="Максимум Agent runs"
                type="number"
                min="1"
                max="8"
                value={value.agent_max_runs}
                onChange={(e) =>
                  change({ agent_max_runs: Number(e.target.value) })
                }
              />
            </label>
            <label>
              Максимум Agent steps / task
              <input
                aria-label="Максимум Agent steps"
                type="number"
                min="1"
                max="50"
                value={value.agent_max_steps}
                onChange={(e) =>
                  change({ agent_max_steps: Number(e.target.value) })
                }
              />
            </label>
            <p>
              {status?.agent_max_steps_supported
                ? "Provider max_steps включён для аккаунта."
                : "Бюджет soft/local: max_steps у провайдера не подтверждён. Возможен перерасход до подтверждения отмены."}
            </p>
            <p>
              Лимит задачи: {status?.task_limits?.agent_steps ?? "?"} steps · $
              {status?.task_limits?.paid_budget ?? "?"} paid.
            </p>
          </fieldset>
          <fieldset>
            <legend>TinyFish Browser · Paid</legend>
            <p>
              Browser: {status?.configured ? "Configured" : "Not configured"} /
              Paid. Alex создаёт cloud Chromium, подключает Playwright/CDP и
              выполняет typed actions. LLM не получает raw JS/CDP.
            </p>
            <label>
              Browser{" "}
              <select
                aria-label="TinyFish Browser mode"
                value={value.browser_mode}
                onChange={(e) => {
                  const browser_mode = e.target.value as WebMode;
                  change({
                    browser_mode,
                    browser_enabled: browser_mode !== "off",
                  });
                }}
              >
                <option value="off">Off</option>
                <option value="auto">Auto</option>
                <option value="on">On</option>
              </select>
            </label>
            <p>
              Оценка: {"$" + (status?.browser_minute_price ?? "?")}/мин. Stop
              закрывает remote session. External side effects требуют
              confirmation policy.
            </p>
            <p>
              {status?.browser_delete_supported
                ? "Stop отправляет завершение сессии провайдеру. Если подтверждение не получено, сессия может продолжать тарифицироваться до timeout."
                : "Провайдер не подтверждает явное удаление: Stop прекращает локальные команды; supplier может тарифицировать до часа простоя."}
            </p>
            <label>
              Бюджет задачи TinyFish, $
              <input
                aria-label="Paid TinyFish task budget"
                type="number"
                min="0.01"
                max="2"
                step="0.01"
                value={value.tinyfish_paid_task_budget_usd}
                onChange={(e) =>
                  change({
                    tinyfish_paid_task_budget_usd: Number(e.target.value),
                  })
                }
              />
            </label>
            <label>
              Максимум Browser sessions / task
              <input
                aria-label="Максимум Browser sessions"
                type="number"
                min="1"
                max="8"
                value={value.browser_max_sessions}
                onChange={(e) =>
                  change({ browser_max_sessions: Number(e.target.value) })
                }
              />
            </label>
            <label>
              Максимум Browser minutes / task
              <input
                aria-label="Максимум Browser minutes"
                type="number"
                min="1"
                max="30"
                value={value.browser_max_minutes}
                onChange={(e) =>
                  change({ browser_max_minutes: Number(e.target.value) })
                }
              />
            </label>
            <p>
              Лимит задачи: {status?.task_limits?.browser_minutes ?? "?"} min ·
              ${status?.task_limits?.paid_budget ?? "?"}.
            </p>
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
              Tor Network: {status?.tor_status || "Проверка…"}
              {status && !status.tor_search_configured
                ? " · Tor Search provider not configured"
                : ""}
            </p>
            <p>
              Tor Browser:{" "}
              {status?.tor_browser?.detected ? "Detected" : "Not detected"}
            </p>
            <p>
              Browser Automation:{" "}
              {status?.tor_browser?.automation === "available"
                ? "Available"
                : status?.tor_browser?.automation === "disabled"
                  ? "Disabled"
                  : status?.tor_browser?.automation === "error"
                    ? "Error"
                    : "Unsupported"}
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
              локальных задач. Direct fallback нет. Tor Browser automation —
              только read-only fallback, если HTTP-fetch недостаточен.
            </p>
            <label>
              Tor Browser fallback{" "}
              <select
                aria-label="Tor Browser automation mode"
                value={value.tor_browser_mode}
                onChange={(e) =>
                  change({
                    tor_browser_mode: e.target.value as TorMode,
                  })
                }
              >
                <option value="off">Off</option>
                <option value="auto">Auto</option>
                <option value="on">On</option>
              </select>
            </label>
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
            <label>
              <input
                type="checkbox"
                aria-label="Auto commit after verification"
                checked={value.auto_commit}
                onChange={(e) => change({ auto_commit: e.target.checked })}
              />{" "}
              Auto commit after successful verification (default off)
            </label>
            <label>
              <input
                type="checkbox"
                aria-label="Allow push for this computer"
                checked={value.allow_push}
                onChange={(e) => change({ allow_push: e.target.checked })}
              />{" "}
              Allow git push after confirmation (default off)
            </label>
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
