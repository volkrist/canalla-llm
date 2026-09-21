import { useState, type FormEvent } from "react";
import { ArrowRight, LockKeyhole, Settings2 } from "lucide-react";
import { authErrorMessage, authenticate, bootstrapOwner } from "../lib/auth";
import { isTauriRuntime } from "../lib/backend";
import type { User } from "../types";
import Brand from "./Brand";

export default function AuthScreen({
  base,
  firstRun,
  onLogin,
  onSettings,
}: {
  base: string;
  firstRun?: boolean;
  onLogin: (token: string, user: User) => void;
  onSettings: () => void;
}) {
  const [ownerMode, setOwnerMode] = useState(Boolean(firstRun));
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const bootstrap = firstRun && ownerMode;
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = bootstrap
        ? await bootstrapOwner(base, email, password, displayName)
        : await authenticate(base, mode, email, password);
      onLogin(result.access_token, result.user);
    } catch (e) {
      setError(authErrorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  const title = bootstrap
    ? "Создайте владельца Alex"
    : mode === "login"
      ? "С возвращением"
      : "Начнём знакомство";
  const subtitle = bootstrap
    ? "Первый аккаунт на этом компьютере станет владельцем Alex и сможет запускать AI."
    : mode === "login"
      ? "Войдите, чтобы продолжить разговор."
      : "Создайте аккаунт для ваших диалогов.";
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
          <h2>{title}</h2>
          <p>{subtitle}</p>
          {!bootstrap && (
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
          )}
          {bootstrap && (
            <label>
              Имя владельца (необязательно)
              <input
                type="text"
                autoComplete="name"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="Alex"
                maxLength={80}
                disabled={busy}
              />
            </label>
          )}
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
                bootstrap
                  ? "new-password"
                  : mode === "login"
                    ? "current-password"
                    : "new-password"
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
              : bootstrap
                ? "Создать владельца Alex"
                : mode === "login"
                  ? "Войти в Alex LLM"
                  : "Создать аккаунт"}
            <ArrowRight size={18} />
          </button>
          <div className="auth-note">
            <LockKeyhole size={14} />
            {bootstrap
              ? "Сеанс сохранится на этом устройстве — повторный вход не понадобится."
              : "История доступна только вашему аккаунту."}
          </div>
          {firstRun && ownerMode && (
            <button
              className="auth-note-link"
              type="button"
              disabled={busy}
              onClick={() => {
                setOwnerMode(false);
                setMode("login");
                setError("");
              }}
            >
              Уже есть аккаунт? Войти как обычно
            </button>
          )}
          {firstRun && !isTauriRuntime() && (
            <p className="muted">
              Создание владельца доступно в установленном приложении Alex.
            </p>
          )}
        </form>
      </div>
    </div>
  );
}
