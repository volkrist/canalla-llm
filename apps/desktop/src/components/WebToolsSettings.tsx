import { useEffect, useState } from "react";
import type { Api } from "../lib/api";
import type { WebMode } from "../lib/tools";

interface Preferences {
  search_enabled: boolean;
  fetch_enabled: boolean;
  default_mode: WebMode;
  agent_enabled: boolean;
  agent_run_budget: number;
  agent_daily_budget: number;
  agent_max_runtime: number;
  browser_enabled: boolean;
}
interface Status {
  agent_read_only_enforced: boolean;
  configured: boolean;
  search_fetch_free: boolean;
  agent_step_price: number;
  browser_minute_price: number;
  agent_max_steps_supported: boolean;
  browser_delete_supported: boolean;
  limits: Record<string, number>;
}
export default function WebToolsSettings({ api }: { api: Api }) {
  const [value, setValue] = useState<Preferences | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    let live = true;
    void Promise.all([
      api.json<Preferences>("/tools/preferences"),
      api.json<Status>("/tools/status"),
    ])
      .then(([prefs, state]) => {
        if (live) {
          setValue(prefs);
          setStatus(state);
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
