// "Something the user is waiting for is running right now."
//
// The update installer must not restart the app in the middle of a generation, a local task, a
// backup or a restore. Producers mark themselves here; the update panel reads the same flag, so no
// component has to guess what another one is doing.

import { useEffect, useState } from "react";

export type BusyKind = "generation" | "local-task" | "backup" | "restore";

const holders = new Set<BusyKind>();
const listeners = new Set<(busy: boolean) => void>();

function publish(): void {
  const busy = holders.size > 0;
  for (const listener of listeners) listener(busy);
}

export function markBusy(kind: BusyKind): void {
  if (holders.has(kind)) return;
  holders.add(kind);
  publish();
}

export function clearBusy(kind: BusyKind): void {
  if (!holders.delete(kind)) return;
  publish();
}

export function isBusy(): boolean {
  return holders.size > 0;
}

export function busyKinds(): BusyKind[] {
  return [...holders];
}

export function subscribeBusy(listener: (busy: boolean) => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Test seam: the registry is process-wide, so a test must be able to reset it. */
export function resetBusy(): void {
  holders.clear();
  publish();
}

export function useBusy(): boolean {
  const [busy, setBusy] = useState(isBusy());
  useEffect(() => subscribeBusy(setBusy), []);
  return busy;
}
