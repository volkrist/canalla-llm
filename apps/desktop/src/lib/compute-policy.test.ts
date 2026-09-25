import { describe, expect, it } from "vitest";
import {
  appliedSelection,
  appliedStrategy,
  COMPUTE_POLICY_DEFAULTS,
  COMPUTE_STRATEGIES,
  manualNeedsGpu,
  pinMissing,
  policyForWire,
  policyIsActionable,
  policyRefusal,
  type ComputePolicyShape,
  type ComputeStrategy,
} from "./compute-policy";

const automatic = (gpu: string | null = null): ComputePolicyShape => ({
  selection: "automatic",
  gpu_id: gpu,
});
const manual = (gpu: string | null): ComputePolicyShape => ({
  selection: "manual",
  gpu_id: gpu,
});
const withStrategy = (
  strategy: ComputeStrategy,
  gpu: string | null = null,
): ComputePolicyShape => ({ selection: "automatic", strategy, gpu_id: gpu });

describe("the automatic default", () => {
  it("seeds no GPU at all, so no name can leak into the form or the wire", () => {
    expect(COMPUTE_POLICY_DEFAULTS.gpu_id).toBeNull();
    expect(COMPUTE_POLICY_DEFAULTS.selection).toBe("automatic");
  });

  it("is never an error, which is what the operator's red banner actually was", () => {
    expect(manualNeedsGpu(automatic())).toBe(false);
    expect(policyRefusal(automatic())).toBe("");
    expect(policyIsActionable(automatic())).toBe(true);
  });

  it("hydrates the server's own answer unchanged: automatic in, automatic out, no pin", () => {
    const served: ComputePolicyShape = { selection: "automatic", gpu_id: null };
    expect(policyForWire(served)).toMatchObject({
      selection: "automatic",
      gpu_id: null,
    });
    expect(policyRefusal(policyForWire(served))).toBe("");
  });

  it("drops a pin left over from an earlier manual choice", () => {
    // The user picks NVIDIA L40S manually, then switches back to automatic. The pin must not travel
    // with the request: automatic means "any compatible card", and a hidden pin would silently
    // contradict the mode the user just chose.
    expect(policyForWire(automatic("NVIDIA L40S"))).toMatchObject({
      selection: "automatic",
      gpu_id: null,
    });
  });

  it("leaves every other field exactly as it was", () => {
    const policy = {
      selection: "automatic" as const,
      gpu_id: "NVIDIA L40S",
      min_vram_gb: 48,
      max_hourly_price: 2,
      session_budget: 3,
      allow_community: false,
    };
    expect(policyForWire(policy)).toEqual({ ...policy, gpu_id: null });
  });
});

describe("the manual pin", () => {
  it("is refused when nothing is named, with a sentence instead of a 422", () => {
    expect(manualNeedsGpu(manual(null))).toBe(true);
    expect(manualNeedsGpu(manual(""))).toBe(true);
    expect(policyRefusal(manual(null))).toMatch(/автоматический выбор/);
    expect(policyIsActionable(manual(null))).toBe(false);
  });

  it("is refused the same way for an empty string as for null", () => {
    // The old free-text field turned a cleared input into `null`, but a stored value can also arrive
    // as an empty string; both mean "nothing named".
    expect(policyForWire(manual(""))).toMatchObject({
      selection: "manual",
      gpu_id: null,
    });
    expect(manualNeedsGpu({ selection: "manual", gpu_id: "" })).toBe(true);
  });

  it("works once a card is actually named", () => {
    const pinned = manual("NVIDIA L40S");
    expect(manualNeedsGpu(pinned)).toBe(false);
    expect(policyRefusal(pinned)).toBe("");
    expect(policyIsActionable(pinned)).toBe(true);
    expect(policyForWire(pinned)).toMatchObject({
      selection: "manual",
      gpu_id: "NVIDIA L40S",
    });
  });
});

describe("the allocation strategy", () => {
  it("defaults to balanced and pins nothing", () => {
    // The one strategy that prefers what is actually bookable over a few cents, and the only
    // sensible starting point for a user who never opens the advanced section.
    expect(COMPUTE_POLICY_DEFAULTS.strategy).toBe("balanced");
    expect(COMPUTE_POLICY_DEFAULTS.gpu_id).toBeNull();
    expect(policyRefusal(withStrategy("balanced"))).toBe("");
  });

  it("is never an error for any of the three ordering values", () => {
    for (const strategy of ["balanced", "fastest", "cheapest"] as const) {
      const policy = withStrategy(strategy);
      expect(pinMissing(policy)).toBe(false);
      expect(policyRefusal(policy)).toBe("");
      expect(policyIsActionable(policy)).toBe(true);
    }
  });

  it("is refused when the manual strategy asks for a card it does not have", () => {
    // Exactly the state the GPU-selection control would refuse, reached through the other control.
    const policy = withStrategy("manual");
    expect(pinMissing(policy)).toBe(true);
    expect(manualNeedsGpu(policy)).toBe(true);
    expect(policyIsActionable(policy)).toBe(false);
    expect(policyRefusal(policy)).toMatch(/Сбалансированной/);
  });

  it("keeps a pin for the manual strategy even when the selection says automatic", () => {
    expect(policyForWire(withStrategy("manual", "NVIDIA L40S"))).toMatchObject({
      selection: "automatic",
      strategy: "manual",
      gpu_id: "NVIDIA L40S",
    });
  });

  it("drops a leftover pin for the three ordering strategies", () => {
    for (const strategy of ["balanced", "fastest", "cheapest"] as const) {
      expect(
        policyForWire(withStrategy(strategy, "NVIDIA L40S")),
      ).toMatchObject({
        strategy,
        gpu_id: null,
      });
    }
  });

  it("keeps a pin the user set through the GPU selection, whatever the order", () => {
    // "Only this card, cheapest first" is a coherent request and must survive the wire unchanged.
    expect(
      policyForWire({
        selection: "manual",
        strategy: "cheapest",
        gpu_id: "NVIDIA L40S",
      }),
    ).toMatchObject({
      selection: "manual",
      strategy: "cheapest",
      gpu_id: "NVIDIA L40S",
    });
  });
});

describe("the panel's own transitions", () => {
  it("offers exactly the four strategies the backend accepts", () => {
    // The labels may change; this set may not, or a submission would be refused as malformed.
    expect(COMPUTE_STRATEGIES.map((item) => item.value)).toEqual([
      "balanced",
      "fastest",
      "cheapest",
      "manual",
    ]);
    expect(COMPUTE_STRATEGIES.every((item) => item.label.length > 0)).toBe(
      true,
    );
  });

  it("switches the GPU control with the manual strategy instead of demanding one choice twice", () => {
    const next = appliedStrategy(automatic(), "manual");
    expect(next).toMatchObject({ strategy: "manual", selection: "manual" });
    // And it is still refused until a card is named, which is the one real precondition.
    expect(pinMissing(next)).toBe(true);
  });

  it("leaves the GPU control alone for the three orderings", () => {
    const pinned = { selection: "manual" as const, gpu_id: "NVIDIA L40S" };
    for (const strategy of ["balanced", "fastest", "cheapest"] as const) {
      expect(appliedStrategy(pinned, strategy)).toMatchObject({
        selection: "manual",
        gpu_id: "NVIDIA L40S",
        strategy,
      });
    }
  });

  it("moves the strategy off manual when the GPU control goes back to automatic", () => {
    // Otherwise the panel would keep demanding a card for a mode that never uses one.
    const chosen: ComputePolicyShape = {
      selection: "manual",
      strategy: "manual",
      gpu_id: "NVIDIA L40S",
    };
    const next = appliedSelection(chosen, "automatic");
    expect(next).toMatchObject({
      selection: "automatic",
      strategy: "balanced",
      gpu_id: null,
    });
    expect(policyIsActionable(next)).toBe(true);
  });

  it("keeps a card that is already chosen when the GPU control goes to manual", () => {
    expect(
      appliedSelection(
        { selection: "automatic", gpu_id: "NVIDIA L40S" },
        "manual",
      ),
    ).toMatchObject({ selection: "manual", gpu_id: "NVIDIA L40S" });
  });
});
