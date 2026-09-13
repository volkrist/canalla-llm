import { useState, type FormEvent } from "react";
import { ArrowRight, LockKeyhole, Settings2 } from "lucide-react";
import { Api } from "../lib/api";
import type { User } from "../types";
import Brand from "./Brand";

export default function AuthScreen({
  base,
  onLogin,
  onSettings,
}: {
  base: string;
  onLogin: (token: string, user: User) => void;
  onSettings: () => void;
}) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const { access_token } = await new Api(base, null).auth(
        mode,
        email,
        password,
      );
      const user = await new Api(base, access_token).me();
      onLogin(access_token, user);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось войти");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="auth-page">
      <div className="auth-story">
        <Brand large />
        <div>
          <span className="eyebrow">ВАШЕ ПРОСТРАНСТВО ДЛЯ ДИАЛОГА</span>
          <h1>
            Хорошие идеи
            <br />
            начинаются
            <br />
            <em>с разговора.</em>
          </h1>
          <p>
            Думайте, задавайте вопросы и возвращайтесь
            <br className="desktop-only" /> к важному. Всё в одном спокойном
            пространстве.
          </p>
        </div>
        <div className="story-footer">
          <span className="tiny-dot" /> DESKTOP · EARLY ACCESS{" "}
          <span>01 / MVP</span>
        </div>
      </div>
      <div className="auth-panel">
        <button
          className="icon-button auth-settings"
          aria-label="Settings"
          onClick={onSettings}
          disabled={busy}
        >
          <Settings2 size={20} />
        </button>
        <form className="auth-form" onSubmit={submit}>
          <span className="eyebrow">ALEX LLM</span>
          <h2>{mode === "login" ? "С возвращением" : "Начнём знакомство"}</h2>
          <p>
            {mode === "login"
              ? "Войдите, чтобы продолжить разговор."
              : "Создайте аккаунт для ваших диалогов."}
          </p>
          <div className="auth-tabs">
            <button
              type="button"
              className={mode === "login" ? "active" : ""}
              disabled={busy}
              onClick={() => {
                setMode("login");
                setError("");
              }}
            >
              Войти
            </button>
            <button
              type="button"
              className={mode === "register" ? "active" : ""}
              disabled={busy}
              onClick={() => {
                setMode("register");
                setError("");
              }}
            >
              Регистрация
            </button>
          </div>
          <label>
            Email
            <input
              type="email"
              autoComplete="username"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
              required
              maxLength={254}
              disabled={busy}
            />
          </label>
          <label>
            Пароль
            <input
              type="password"
              autoComplete={
                mode === "login" ? "current-password" : "new-password"
              }
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Минимум 10 символов"
              required
              minLength={10}
              maxLength={128}
              disabled={busy}
            />
          </label>
          {error && (
            <div className="error" role="alert">
              {error}
            </div>
          )}
          <button className="primary auth-submit" disabled={busy}>
            {busy
              ? "Подключаемся…"
              : mode === "login"
                ? "Войти в Alex LLM"
                : "Создать аккаунт"}
            <ArrowRight size={18} />
          </button>
          <div className="auth-note">
            <LockKeyhole size={14} /> История доступна только вашему аккаунту.
          </div>
        </form>
      </div>
    </div>
  );
}
