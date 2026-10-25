// A task can finish (or write its answer) after the stream that started it is gone: the client
// navigated away, the window reloaded, or the turn was detached on purpose. The open window must
// then poll for the late answer instead of showing a message that stays "generating" forever.
//
// Terminal states are the only ones that cannot produce a new answer. Everything else is watched.

const TERMINAL = new Set(["COMPLETED", "FAILED", "STOPPED"]);

export function awaitsLateAnswer(status: string | null | undefined): boolean {
  if (!status) return false;
  return !TERMINAL.has(status);
}
