import { useCallback, useEffect, useMemo, useState } from "react";
import { Api } from "./lib/api";
import {
  backendMessage,
  ensureBackend,
  isTauriRuntime,
  type BackendRuntime,
} from "./lib/backend";
import { loadSettings } from "./lib/settings";
import type { Health, LLMStatus, Settings, User } from "./types";
import AuthScreen from "./components/AuthScreen";
import Workspace from "./components/Workspace";
import SettingsDialog from "./components/SettingsDialog";

export default function App() {
  const [settings, setSettings] = useState(loadSettings);
  const [session, setSession] = useState<{ token: string; user: User } | null>(
    null,
  );
  const [health, setHealth] = useState<Health | null>(null);
  const [llm, setLlm] = useState<LLMStatus | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [runtime, setRuntime] = useState<BackendRuntime | null>(null);
  const api = useMemo(
    () => new Api(settings.backendUrl, session?.token || null),
    [settings.backendUrl, session?.token],
  );
  const logout = useCallback(() => setSession(null), []);
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const next = await ensureBackend();
        if (cancelled || !next) return;
        setRuntime(next);
        if (next.state === "ready" && next.url) {
          const url = next.url;
          setSettings((prev) =>
            prev.backendUrl === url ? prev : { ...prev, backendUrl: url },
          );
        }
      } catch {
        if (!cancelled) {
          setRuntime({
            state: "error",
            ownership: "none",
            error: "backend_error",
            data_dir: "",
          });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);
  useEffect(() => {
    let cancelled = false;
    setLlm(null);
    if (!session) return;
    const check = async () => {
      try {
        const next = await api.json<LLMStatus>("/llm/status");
        if (!cancelled) setLlm(next);
      } catch {
        if (!cancelled) setLlm(null);
      }
    };
    void check();
    const timer = setInterval(() => void check(), 3000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [api, session]);
  useEffect(() => {
    document.documentElement.dataset.theme = settings.theme;
  }, [settings.theme]);
  useEffect(() => {
    let cancelled = false;
    const publicApi = new Api(settings.backendUrl, null);
    setHealth(null);
    const check = async () => {
      try {
        const data = await publicApi.health();
        if (!cancelled) setHealth(data);
      } catch {
        if (!cancelled) setHealth(null);
      }
    };
    void check();
    const timer = setInterval(() => void check(), 10000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [settings.backendUrl]);
  function saveSettings(next: Settings) {
    localStorage.setItem("alex-settings", JSON.stringify(next));
    if (next.backendUrl !== settings.backendUrl) logout();
    setSettings(next);
    setShowSettings(false);
  }
  const banner = backendMessage(runtime);
  const tauriBlocked = isTauriRuntime() && runtime?.state !== "ready";
  return (
    <>
      {banner ? (
        <div
          className={
            runtime?.state === "error"
              ? "runtime-banner error"
              : "runtime-banner"
          }
          role="status"
        >
          {banner}
        </div>
      ) : null}
      {tauriBlocked ? (
        runtime?.state === "error" ? null : (
          <div className="runtime-wait">Запуск Alex…</div>
        )
      ) : session ? (
        <Workspace
          key={session.token}
          api={api}
          user={session.user}
          health={health}
          llm={llm}
          settings={settings}
          onLogout={logout}
          onSettings={() => setShowSettings(true)}
        />
      ) : (
        <AuthScreen
          key={settings.backendUrl}
          base={settings.backendUrl}
          onLogin={(token, user) => setSession({ token, user })}
          onSettings={() => setShowSettings(true)}
        />
      )}
      {showSettings && (
        <SettingsDialog
          api={session ? api : undefined}
          userId={session?.user.id}
          value={settings}
          onSave={saveSettings}
          onClose={() => setShowSettings(false)}
        />
      )}
    </>
  );
}
