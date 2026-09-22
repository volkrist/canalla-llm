// One device snapshot, one reader.
//
// The header chip, the compact Computer bar and Settings used to read the local device on
// independent schedules, so they could disagree for up to one polling interval. The workspace's
// device loop is the only reader now: it publishes what it read, and every other view
// subscribes. Nothing here calls the host, and nothing here starts a second timer.
//
// An event asks for a fresh read (the Connect action and the host job loop) so a user action
// still lands immediately instead of waiting for the next tick.

import { useSyncExternalStore } from "react";
import type { DeviceStatus } from "./host";

const UNKNOWN: DeviceStatus = { paired: false, online: false };
let snapshot: DeviceStatus = UNKNOWN;
const listeners = new Set<() => void>();

/** Published by the single device loop in the workspace. */
export function publishDevice(next: DeviceStatus): void {
  snapshot = next;
  for (const listener of listeners) listener();
}

export function deviceSnapshot(): DeviceStatus {
  return snapshot;
}

export function subscribeDevice(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** The shared snapshot. Subscribing never starts a read. */
export function useDeviceStatus(): DeviceStatus {
  return useSyncExternalStore(subscribeDevice, deviceSnapshot, deviceSnapshot);
}

export const DEVICE_REFRESH_EVENT = "alex-device-refresh";

/** Ask the one device loop for a fresh read (a user action, not a second poller). */
export function requestDeviceRefresh(): void {
  window.dispatchEvent(new Event(DEVICE_REFRESH_EVENT));
}
