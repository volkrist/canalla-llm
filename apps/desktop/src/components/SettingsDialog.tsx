import { useEffect, useRef, useState, type FormEvent } from "react";
import { X } from "lucide-react";
import type { Settings } from "../types";
import { validateBackendUrl } from "../lib/api";
import type { Api } from "../lib/api";
import { exportChats } from "../lib/files";
import { clearDrafts, draftPrefix } from "../lib/drafts";
import EmbeddingModelStatus from "./EmbeddingModelStatus";
import WebToolsSettings from "./WebToolsSettings";
import ProviderSecretPanel from "./ProviderSecretPanel";
import { version } from "../../package.json";

export default function SettingsDialog({
  value,
  onSave,
  onClose,
  api,
  userId,
}: {
  value: Settings;
  onSave: (settings: Settings) => void;
  onClose: () => void;
  api?: Api;
  userId?: string;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [url, setUrl] = useState(value.backendUrl);
  const [fontSize, setFontSize] = useState(value.fontSize);
  const [error, setError] = useState("");
  const [section, setSection] = useState("Общие");
  const [options, setOptions] = useState(value);
  const [clear, setClear] = useState(false);
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  useEffect(() => {
    window.addEventListener("alex-browser-advanced", onClose);
    return () => window.removeEventListener("alex-browser-advanced", onClose);
  }, [onClose]);
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
      <form onSubmit={save}>
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
        <nav className="settings-tabs">
          {[
            "Общие",
            "Чат",
            "AI / Compute",
            "Personalization / Memory",
            "Files / RAG",
            "Web & Tools",
            "Данные",
            "Дополнительно",
            "О программе",
          ].map((tab) => (
            <button
              type="button"
              aria-pressed={section === tab}
              key={tab}
              onClick={() => setSection(tab)}
            >
              {tab}
            </button>
          ))}
        </nav>
        {section === "О программе" && <p>Alex LLM {version}</p>}
        {section === "Web & Tools" &&
          (api ? <WebToolsSettings api={api} /> : <p>Войдите в аккаунт.</p>)}
        {section === "Files / RAG" &&
          (api ? (
            <EmbeddingModelStatus api={api} />
          ) : (
            <p>Войдите в аккаунт.</p>
          ))}
        {section === "Personalization / Memory" && (
          <button
            type="button"
            disabled={!api}
            onClick={() => {
              onClose();
              window.dispatchEvent(new Event("alex-personal-settings"));
            }}
          >
            Профиль, инструкции и настройки памяти
          </button>
        )}
        {section === "AI / Compute" && (
          <>
            <p>Выбор GPU, лимит цены, бюджет сессии и автоостановка.</p>
            <ProviderSecretPanel />
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
                  setOptions({ ...options, technicalDetails: e.target.checked })
                }
              />
              Показывать технические сведения
            </label>
          </>
        )}
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
                {clear && (
                  <div>
                    <p>Удалить черновики этого аккаунта на устройстве?</p>
                    <button
                      type="button"
                      onClick={() => {
                        if (userId) clearDrafts(draftPrefix(api.base, userId));
                        window.dispatchEvent(new Event("alex-drafts-cleared"));
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
    </dialog>
  );
}
