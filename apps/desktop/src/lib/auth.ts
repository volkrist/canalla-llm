import type { User } from "../types";
import { Api } from "./api";
import { isTauriRuntime } from "./backend";

export type AuthPhase =
  | "unknown"
  | "restoring"
  | "authenticated"
  | "anonymous"
  | "first_run"
  | "error";

export interface AuthResult {
  access_token: string;
  user: User;
  session_id: string | null;
}

export function authErrorMessage(error: unknown): string {
  const message = String(error);
  if (message.startsWith("backend_rejected:"))
    return message.slice("backend_rejected:".length);
  if (message === "backend_unreachable")
    return "Локальный сервер недоступен. Подождите и попробуйте снова.";
  if (message === "invalid_credentials")
    return "Пароль должен быть от 10 до 128 символов.";
  if (message === "session_rejected")
    return "Сеанс больше недействителен. Войдите снова.";
  return error instanceof Error ? error.message : "Не удалось войти";
}

/** Login/register. In Tauri the call (and the refresh secret) stays in the
 *  native layer; the browser dev fallback keeps the legacy memory-only flow. */
export async function authenticate(
  base: string,
  mode: "login" | "register",
  email: string,
  password: string,
): Promise<AuthResult> {
  if (isTauriRuntime()) {
    const { invoke } = await import("@tauri-apps/api/core");
    return invoke<AuthResult>(
      mode === "login" ? "auth_login" : "auth_register",
      {
        backendUrl: base,
        email,
        password,
      },
    );
  }
  const { access_token } = await new Api(base, null).auth(
    mode,
    email,
    password,
  );
  const user = await new Api(base, access_token).me();
  return { access_token, user, session_id: null };
}

/** First-owner bootstrap: requires the local runtime proof held by Desktop. */
export async function bootstrapOwner(
  base: string,
  email: string,
  password: string,
  displayName: string,
): Promise<AuthResult> {
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<AuthResult>("auth_bootstrap", {
    backendUrl: base,
    email,
    password,
    displayName,
  });
}

/** Restore the persistent device session (rotates the refresh credential).
 *  Returns null when there is no session or the session was rejected. */
export async function restoreSession(base: string): Promise<AuthResult | null> {
  if (!isTauriRuntime()) return null;
  const { invoke } = await import("@tauri-apps/api/core");
  try {
    return await invoke<AuthResult>("auth_restore", { backendUrl: base });
  } catch (error) {
    const message = String(error);
    if (message === "no_session" || message === "session_rejected") return null;
    throw error;
  }
}

/** Refresh hook for the Api client: returns a fresh access token or null. */
export async function refreshAccessToken(base: string): Promise<string | null> {
  const result = await restoreSession(base);
  return result ? result.access_token : null;
}

/** Revoke the persistent session server-side (best effort) and clear the
 *  local credential. */
export async function logoutSession(base: string): Promise<void> {
  if (!isTauriRuntime()) return;
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("auth_logout", { backendUrl: base }).catch(() => undefined);
}

export async function authState(
  base: string,
): Promise<"first_run" | "auth_required"> {
  const state = await new Api(base, null).json<{
    state: string;
    users_exist: boolean;
  }>("/auth/state");
  return state.state === "first_run" ? "first_run" : "auth_required";
}
