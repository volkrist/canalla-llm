export function draftPrefix(backend: string, user: string) {
  return `alex-draft:${encodeURIComponent(backend)}:${user}:`;
}
export function readDraft(prefix: string, chat: string | null) {
  try {
    return localStorage.getItem(prefix + (chat || "new")) || "";
  } catch {
    return "";
  }
}
export function writeDraft(prefix: string, chat: string | null, value: string) {
  try {
    if (value) localStorage.setItem(prefix + (chat || "new"), value);
    else localStorage.removeItem(prefix + (chat || "new"));
  } catch {
    /* A full browser store must not prevent writing a message. */
  }
}
export function clearDrafts(prefix: string) {
  Object.keys(localStorage)
    .filter((key) => key.startsWith(prefix))
    .forEach((key) => localStorage.removeItem(key));
}
