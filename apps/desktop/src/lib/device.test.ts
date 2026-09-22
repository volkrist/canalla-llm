// The device snapshot is shared, not re-read: a subscriber must never start its own read, and
// the Connect action must ask the one loop for a fresh one instead of polling.

import { afterEach, describe, expect, it, vi } from "vitest";
import {
  DEVICE_REFRESH_EVENT,
  deviceSnapshot,
  publishDevice,
  requestDeviceRefresh,
  subscribeDevice,
} from "./device";

afterEach(() => {
  publishDevice({ paired: false, online: false });
});

describe("device store", () => {
  it("starts unknown and never claims a device before one is published", () => {
    expect(deviceSnapshot()).toEqual({ paired: false, online: false });
  });

  it("publishes the read status to every subscriber", () => {
    const seen: string[] = [];
    const stop = subscribeDevice(() =>
      seen.push(deviceSnapshot().display_name || ""),
    );
    publishDevice({
      paired: true,
      online: true,
      display_name: "Windows device",
    });
    publishDevice({
      paired: true,
      online: false,
      display_name: "Windows device",
    });
    stop();
    publishDevice({ paired: false, online: false });
    expect(seen).toEqual(["Windows device", "Windows device"]);
    expect(deviceSnapshot()).toEqual({ paired: false, online: false });
  });

  it("subscription alone reads nothing: no host call, no timer", () => {
    const read = vi.fn();
    const stop = subscribeDevice(read);
    stop();
    expect(read).not.toHaveBeenCalled();
  });

  it("asks the running device loop for a refresh by event", () => {
    const events: string[] = [];
    const target = globalThis as unknown as {
      window?: { dispatchEvent: (event: Event) => boolean };
    };
    const previous = target.window;
    target.window = {
      dispatchEvent: (event: Event) => {
        events.push(event.type);
        return true;
      },
    };
    try {
      requestDeviceRefresh();
    } finally {
      target.window = previous;
    }
    expect(events).toEqual([DEVICE_REFRESH_EVENT]);
  });
});
