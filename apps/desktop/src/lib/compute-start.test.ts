import { describe, expect, it, vi } from "vitest";
import { startCompute } from "./compute-start";

describe("compute start routing", () => {
  it("uses the Gateway ensure in shared mode and never the blocked local search", async () => {
    const savePreferences = vi.fn(async () => {});
    const ensureGateway = vi.fn(async () => {});
    const searchLocal = vi.fn(async () => {});

    const route = await startCompute({
      shared: true,
      savePreferences,
      ensureGateway,
      searchLocal,
    });

    expect(route).toBe("gateway");
    expect(savePreferences).toHaveBeenCalledTimes(1);
    expect(ensureGateway).toHaveBeenCalledTimes(1);
    // `/compute/search` answers 409 gateway_managed_compute in this mode.
    expect(searchLocal).not.toHaveBeenCalled();
  });

  it("keeps the local quote/search flow in direct mode", async () => {
    const savePreferences = vi.fn(async () => {});
    const ensureGateway = vi.fn(async () => {});
    const searchLocal = vi.fn(async () => {});

    const route = await startCompute({
      shared: false,
      savePreferences,
      ensureGateway,
      searchLocal,
    });

    expect(route).toBe("local");
    expect(savePreferences).toHaveBeenCalledTimes(1);
    expect(searchLocal).toHaveBeenCalledTimes(1);
    expect(ensureGateway).not.toHaveBeenCalled();
  });

  it("saves the user's limits before starting anything", async () => {
    const order: string[] = [];
    await startCompute({
      shared: true,
      savePreferences: async () => {
        order.push("save");
      },
      ensureGateway: async () => {
        order.push("ensure");
      },
      searchLocal: async () => {
        order.push("local");
      },
    });
    expect(order).toEqual(["save", "ensure"]);
  });
});
