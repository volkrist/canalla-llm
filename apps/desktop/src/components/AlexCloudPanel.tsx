import { useState } from "react";
import { isTauriRuntime } from "../lib/backend";
import {
  cloudLabel,
  disconnectGateway,
  ensureCloudCompute,
  enrollError,
  enrollGateway,
  sessionSummary,
  stopCloudCompute,
  useCloud,
  type CloudComputeStatus,
  type RestartOutcome,
} from "../lib/cloud";

function restartMessage(prefix: string, outcome: RestartOutcome): string {
  if (outcome === "ok") return `${prefix}, локальный сервер перезапущен.`;
  if (outcome === "external") {
    return `${prefix}; перезапустите внешний backend вручную.`;
  }
  return `${prefix}, но локальный сервер не перезапустился. Запустите Canalla LLM заново.`;
}

/** Alex Cloud: one installation connects to the shared Gateway, and the Gateway
 *  holds the only RunPod account. No RunPod key, no installation secret and no
 *  access token is readable here; `installation_id` is a public identifier. */
export default function AlexCloudPanel() {
  const tauri = isTauriRuntime();
  const cloud = useCloud();
  const [code, setCode] = useState("");
  const [address, setAddress] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [compute, setCompute] = useState<CloudComputeStatus | null>(null);
  const [computeMessage, setComputeMessage] = useState("");
  const gateway = cloud.gateway;
  const status = cloud.status;
  const configured = gateway?.configured === true || status?.enrolled === true;
  const defaultUrl = status?.default_url ?? gateway?.default_url ?? "";
  const url = address ?? status?.url ?? gateway?.url ?? defaultUrl;
  const installation =
    status?.installation_id ?? gateway?.installation_id ?? "";
  const note = status?.message || gateway?.message || "";
  const stateText = status
    ? cloudLabel(status.state)
    : cloud.error
      ? "Статус недоступен"
      : "проверяем…";
  async function connect() {
    setBusy(true);
    setMessage("");
    try {
      const restart = await enrollGateway(url.trim(), code);
      setCode("");
      setMessage(restartMessage("Canalla Cloud подключён", restart));
    } catch (e) {
      setMessage(enrollError(e));
    } finally {
      setBusy(false);
    }
  }
  function disconnect() {
    const confirmed = window.confirm(
      "Отключить Canalla Cloud на этой установке? Общий Gateway и общий аккаунт RunPod станут недоступны.",
    );
    if (!confirmed) return;
    setBusy(true);
    setMessage("");
    void disconnectGateway()
      .then((restart) =>
        setMessage(restartMessage("Canalla Cloud отключён", restart)),
      )
      .catch((e: unknown) => setMessage(enrollError(e)))
      .finally(() => setBusy(false));
  }
  /** Both calls are typed Gateway operations: the Gateway decides whether money is
   *  spent, and it is the only thing that can create or stop a Pod. */
  async function runCompute(action: "ensure" | "stop") {
    if (
      action === "stop" &&
      !window.confirm(
        "Остановить общий AI? Compute используется всеми установками Canalla Cloud.",
      )
    ) {
      return;
    }
    setBusy(true);
    setComputeMessage("");
    try {
      const result =
        action === "ensure"
          ? await ensureCloudCompute()
          : await stopCloudCompute();
      setCompute(result.compute);
      setComputeMessage(result.compute.message || "");
    } catch (e) {
      setComputeMessage(
        e instanceof Error ? e.message : "Canalla Cloud не принял запрос",
      );
    } finally {
      setBusy(false);
    }
  }
  if (!tauri)
    return (
      <p className="muted">
        Подключение к Canalla Cloud доступно в установленном приложении Canalla
        LLM.
      </p>
    );
  return (
    <div>
      <p role="status">
        <strong>Canalla Cloud</strong> · {stateText}
      </p>
      <p className="field-help">
        RunPod теперь инфраструктура Canalla LLM: обычный пользователь не вводит
        RunPod API key, а одна установка Canalla LLM подключается к общему
        Gateway и использует общий аккаунт RunPod. Ключ RunPod остаётся только
        на сервере.
      </p>
      {cloud.error && <p className="muted">{cloud.error}</p>}
      {note && <p className="muted">{note}</p>}
      {configured ? (
        <>
          {installation && <p className="muted">Установка: {installation}</p>}
          {url && <p className="muted">Gateway: {url}</p>}
          <div>
            <p className="field-help">
              Общий AI запускается по запросу и останавливается сам, когда им
              никто не пользуется. Лимиты расходов — ваши собственные: они
              задаются в разделе настроек «AI / Compute».
            </p>
            <div>
              <button
                type="button"
                disabled={busy}
                onClick={() => void runCompute("ensure")}
              >
                Запустить AI
              </button>
              <button
                type="button"
                disabled={busy || compute?.session?.managed !== true}
                onClick={() => void runCompute("stop")}
              >
                Остановить AI
              </button>
            </div>
            {compute && (
              <p className="muted">
                AI: {compute.ai_label || compute.state}
                {sessionSummary(compute.session)
                  ? ` · ${sessionSummary(compute.session)}`
                  : ""}
              </p>
            )}
            {computeMessage && <p className="muted">{computeMessage}</p>}
          </div>
          <button type="button" disabled={busy} onClick={disconnect}>
            Отключить Canalla Cloud
          </button>
        </>
      ) : (
        <>
          <label>
            Адрес Gateway
            <input
              type="url"
              value={url}
              onChange={(e) => setAddress(e.target.value)}
              placeholder="https://gateway.example"
              autoComplete="off"
              disabled={busy}
            />
          </label>
          <p className="field-help">
            {defaultUrl
              ? "Адрес задан установкой. Меняйте его только по указанию администратора."
              : "Укажите адрес общего Gateway Canalla Cloud."}
          </p>
          <label>
            Код активации
            <input
              type="password"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="Вставьте код активации"
              autoComplete="off"
              disabled={busy}
            />
          </label>
          <button
            type="button"
            disabled={busy || !code.trim() || !url.trim()}
            onClick={() => void connect()}
          >
            Подключить
          </button>
        </>
      )}
      {message && <p className="muted">{message}</p>}
    </div>
  );
}
