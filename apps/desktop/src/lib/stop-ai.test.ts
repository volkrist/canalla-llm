import { describe, expect, it } from "vitest";
import { stopPaidAi } from "./stop-ai";

describe("stopping the paid AI", () => {
  it("aborts the generation before it asks the Gateway to stop", async () => {
    const order: string[] = [];

    await stopPaidAi({
      stopGeneration: () => order.push("generation"),
      stopCompute: async () => {
        order.push("compute");
      },
      refresh: async () => {
        order.push("refresh");
      },
    });

    expect(order).toEqual(["generation", "compute", "refresh"]);
  });
});
