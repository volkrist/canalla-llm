import { useCallback, useEffect, useMemo, useState } from "react";
import { Api } from "./lib/api";
import {
  authErrorMessage,
  authState,
  logoutSession,
  refreshAccessToken,
  restoreSession,
  type AuthPhase,
} from "./lib/auth";
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
  const [phase, setPhase] = useState<AuthPhase>("unknown");
  const [authError, setAuthError] = useState("");
  const [health, setHealth] = useState<Health | null>(null);
  const [llm, setLlm] = useState<LLMStatus | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [runtime, setRuntime] = useState<BackendRuntime | null>(null);
  const api = useMemo(
    () =>
      new Api(settings.backendUrl, session?.token || null, () =>
        refreshAccessToken(settings.backendUrl),
      ),
    [settings.backendUrl, session?.token],
  );
  const startAuth = useCallback(async (url: string) => {
    setPhase("restoring");
    try {
      const restored = await restoreSession(url);
      if (restored) {
        setSession({ token: restored.access_token, user: restored.user });
        setPhase("authenticated");
        return;
      }
      const state = await authState(url);
      setPhase(state === "first_run" ? "first_run" : "anonymous");
    } catch (error) {
      setAuthError(authErrorMessage(error));
      setPhase("error");
    }
  }, []);
  const logout = useCallback(() => {
    void logoutSession(settings.backendUrl);
    setSession(null);
    setPhase("anonymous");
  }, [settings.backendUrl]);
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      if (!isTauriRuntime()) {
        setPhase("anonymous");
        return;
      }
      try {
        const next = await ensureBackend();
        if (cancelled || !next) return;
        setRuntime(next);
        if (next.state === "ready" && next.url) {
          const url = next.url;
          setSettings((prev) =>
            prev.backendUrl === url ? prev : { ...prev, backendUrl: url },
          );
          if (!cancelled) await startAuth(url);
        }
      } catch {
        if (!cancelled) {
          setRuntime({
            state: "error",
            ownership: "none",
            error: "backend_error",
            data_dir: "",
          });
          setPhase("error");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [startAuth]);
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
  const retry = () => void startAuth(settings.backendUrl);
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
          <div className="runtime-wait">Запуск Canalla…</div>
        )
      ) : phase === "authenticated" && session ? (
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
      ) : phase === "restoring" ? (
        <div className="runtime-wait">Восстанавливаем сеанс…</div>
      ) : phase === "error" ? (
        <div className="auth-page">
          <div className="auth-panel">
            <form className="auth-form" onSubmit={retry}>
              <span className="eyebrow">CANALLA LLM</span>
              <h2>Не удалось войти</h2>
              <p>{authError || "Локальный сервер не ответил."}</p>
              <button className="primary auth-submit" type="submit">
                Повторить
              </button>
            </form>
          </div>
        </div>
      ) : (
        <AuthScreen
          key={settings.backendUrl + (phase === "first_run" ? "-first" : "")}
          base={settings.backendUrl}
          firstRun={phase === "first_run"}
          onLogin={(token, user) => {
            setSession({ token, user });
            setPhase("authenticated");
          }}
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
