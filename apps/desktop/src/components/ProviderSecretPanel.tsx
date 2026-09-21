import { useEffect, useState } from "react";
import { isTauriRuntime } from "../lib/backend";

/** Secure RunPod key management. The secret lives only in the OS credential
 *  store (Windows Credential Manager / DPAPI fallback); it is never read
 *  back into the UI. After save/delete the owned backend restarts so the
 *  new environment takes effect. */
export default function ProviderSecretPanel() {
  const tauri = isTauriRuntime();
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  async function refreshStatus() {
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      const result = await invoke<{ configured: boolean }>(
        "provider_secret_configured",
        { name: "runpod" },
      );
      setConfigured(result.configured);
    } catch {
      setConfigured(null);
    }
  }
  useEffect(() => {
    if (tauri) void refreshStatus();
  }, [tauri]);
  async function save() {
    setBusy(true);
    setMessage("");
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      await invoke("set_provider_secret", { name: "runpod", secret: value });
      setValue("");
      setMessage("Ключ сохранён. Перезапускаю локальный сервер…");
      await invoke("restart_backend");
      setMessage("Ключ сохранён, локальный сервер перезапущен.");
      await refreshStatus();
    } catch (e) {
      setMessage(
        String(e) === "backend_not_owned"
          ? "Ключ сохранён; перезапустите внешний backend вручную."
          : `Ошибка: ${String(e)}`,
      );
    } finally {
      setBusy(false);
    }
  }
  async function remove() {
    setBusy(true);
    setMessage("");
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      await invoke("delete_provider_secret", { name: "runpod" });
      setMessage("Ключ удалён. Перезапускаю локальный сервер…");
      await invoke("restart_backend");
      setMessage("Ключ удалён, локальный сервер перезапущен.");
      await refreshStatus();
    } catch (e) {
      setMessage(`Ошибка: ${String(e)}`);
    } finally {
      setBusy(false);
    }
  }
  if (!tauri)
    return (
      <p className="muted">
        Управление ключами доступно в установленном приложении Canalla LLM.
      </p>
    );
  return (
    <div>
      <p className="field-help">
        Ключ RunPod хранится в защищённом хранилище Windows и никогда не
        показывается обратно. Запуск GPU по-прежнему требует подтверждения.
      </p>
      <p>
        Статус:{" "}
        {configured === null
          ? "проверяем…"
          : configured
            ? "ключ задан"
            : "ключ не задан"}
      </p>
      <label>
        RunPod API key
        <input
          type="password"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="Вставьте новый ключ"
          autoComplete="off"
          disabled={busy}
        />
      </label>
      <div>
        <button type="button" disabled={busy || !value.trim()} onClick={save}>
          Сохранить ключ
        </button>
        <button type="button" disabled={busy || !configured} onClick={remove}>
          Удалить ключ
        </button>
      </div>
      {message && <p className="muted">{message}</p>}
    </div>
  );
}
