import { describe, expect, it } from "vitest";
import {
  COMPUTE_POLICY_DEFAULTS,
  manualNeedsGpu,
  policyForWire,
  policyIsActionable,
  policyRefusal,
  type ComputePolicyShape,
} from "./compute-policy";

const automatic = (gpu: string | null = null): ComputePolicyShape => ({
  selection: "automatic",
  gpu_id: gpu,
});
const manual = (gpu: string | null): ComputePolicyShape => ({
  selection: "manual",
  gpu_id: gpu,
});

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
