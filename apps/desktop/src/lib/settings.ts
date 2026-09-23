import type { Settings } from "../types";
import { validateBackendUrl } from "./api";

export function loadSettings(): Settings {
  const defaults: Settings = {
    backendUrl: import.meta.env.VITE_BACKEND_URL || "http://127.0.0.1:8000",
    fontSize: 15,
    theme: "dark",
    language: "ru",
    enterSends: true,
    autoScroll: true,
    timestamps: true,
    technicalDetails: false,
    autoCheckUpdates: true,
  };
  try {
    const stored = JSON.parse(localStorage.getItem("alex-settings") || "{}");
    return {
      ...defaults,
      theme: stored.theme === "system" ? "system" : "dark",
      enterSends: stored.enterSends !== false,
      autoScroll: stored.autoScroll !== false,
      timestamps: stored.timestamps !== false,
      technicalDetails: stored.technicalDetails === true,
      autoCheckUpdates: stored.autoCheckUpdates !== false,
      backendUrl: validateBackendUrl(stored.backendUrl || defaults.backendUrl),
      fontSize: [14, 15, 17, 19].includes(stored.fontSize)
        ? stored.fontSize
        : 15,
    };
  } catch {
    return defaults;
  }
}
