import { useCallback, useEffect, useMemo, useState } from "react";
import { Api } from "./lib/api";
import { loadSettings } from "./lib/settings";
import type { Health, LLMStatus, Settings, User } from "./types";
import AuthScreen from "./components/AuthScreen";
import Workspace from "./components/Workspace";
import SettingsDialog from "./components/SettingsDialog";

export default function App() {
  const [settings, setSettings] = useState(loadSettings);
  // Bearer tokens live in memory only; restarting the application requires login.
  const [session, setSession] = useState<{ token: string; user: User } | null>(
    null,
  );
  const [health, setHealth] = useState<Health | null>(null);
  const [llm, setLlm] = useState<LLMStatus | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const api = useMemo(
    () => new Api(settings.backendUrl, session?.token || null),
    [settings.backendUrl, session?.token],
  );
  const logout = useCallback(() => setSession(null), []);
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
  return (
    <>
      {session ? (
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
