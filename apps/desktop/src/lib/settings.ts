import type { Settings } from "../types";
import { validateBackendUrl } from "./api";

export function loadSettings(): Settings {
  const defaults = {
    backendUrl: import.meta.env.VITE_BACKEND_URL || "http://127.0.0.1:8000",
    fontSize: 15,
  };
  try {
    const stored = JSON.parse(localStorage.getItem("alex-settings") || "{}");
    return {
      backendUrl: validateBackendUrl(stored.backendUrl || defaults.backendUrl),
      fontSize: [14, 15, 17, 19].includes(stored.fontSize)
        ? stored.fontSize
        : 15,
    };
  } catch {
    return defaults;
  }
}
