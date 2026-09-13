import { useEffect, useRef, useState, type FormEvent } from "react";
import { X } from "lucide-react";
import type { Settings } from "../types";
import { validateBackendUrl } from "../lib/api";

export default function SettingsDialog({
  value,
  onSave,
  onClose,
}: {
  value: Settings;
  onSave: (settings: Settings) => void;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [url, setUrl] = useState(value.backendUrl);
  const [fontSize, setFontSize] = useState(value.fontSize);
  const [error, setError] = useState("");
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  function save(event: FormEvent) {
    event.preventDefault();
    try {
      onSave({ backendUrl: validateBackendUrl(url), fontSize });
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
          Адрес вашего сервера. При смене адреса потребуется войти заново. Для
          удалённого сервера используйте HTTPS.
        </p>
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
