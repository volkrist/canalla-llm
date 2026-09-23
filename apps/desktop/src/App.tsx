import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  recoverBackend,
  recoveryDelay,
  type BackendRuntime,
} from "./lib/backend";
import { loadSettings } from "./lib/settings";
import { reconcileAutostart } from "./lib/autostart";
import { useBusy } from "./lib/busy";
import { useUpdates } from "./hooks/useUpdates";
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
  const [recovering, setRecovering] = useState(false);
  const recoveryAttempts = useRef(0);
  // One owner for the update check: startup plus a periodic re-check, never blocking the app.
  const updateBusy = useBusy();
  const updates = useUpdates({
    enabled: settings.autoCheckUpdates,
    busy: updateBusy,
  });
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
      // The login entry follows the stored policy on every launch: a fresh installation registers
      // itself (the default is on) and an explicit «off» removes an entry the machine still has.
      void reconcileAutostart(settings.launchAtLogin);
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
    let recovering = false;
    const recover = async () => {
      if (!isTauriRuntime() || recovering) return;
      const attempt = recoveryAttempts.current;
      if (recoveryDelay(attempt) === null) return;
      recoveryAttempts.current = attempt + 1;
      recovering = true;
      setRecovering(true);
      try {
        const result = await recoverBackend(attempt);
        if (cancelled) return;
        if (result.state === "ready") {
          await check();
          return;
        }
        if (recoveryDelay(recoveryAttempts.current) === null) {
          setRuntime((prev) =>
            prev?.state === "ready"
              ? { ...prev, state: "error", error: "BACKEND_START_FAILED" }
              : prev,
          );
          return;
        }
        recovering = false;
        await recover();
      } finally {
        if (!cancelled) setRecovering(false);
      }
    };
    // A local backend that died must come back by itself: the Desktop owns one restart per
    // session and this asks for it. The attempts are bounded, so a permanent failure ends as an
    // honest error on screen instead of a loop, and a recoverable crash needs no button.
    const check = async () => {
      try {
        const data = await publicApi.health();
        if (cancelled) return;
        setHealth(data);
        recoveryAttempts.current = 0;
      } catch {
        if (cancelled) return;
        setHealth(null);
        void recover();
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
  const banner = recovering ? backendMessage(null) : backendMessage(runtime);
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
          onLogout={logout}
          updates={updates}
        />
      )}
    </>
  );
}
