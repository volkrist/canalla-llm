import { describe, expect, it } from "vitest";
import type { CloudComputeSession, CloudComputeStatus } from "./cloud";
import {
  overLimitRateLine,
  policyMoney,
  runningSharedSession,
} from "./compute";

/** The Gateway's shared compute session, in the shape `/cloud/compute/ensure` returns. */
function session(
  extra: Partial<CloudComputeSession> = {},
): CloudComputeSession {
  return {
    gpu: "NVIDIA L40S",
    hourly_rate_usd: "0.790000",
    budget_usd: "3.000000",
    estimated_usd: "0.210000",
    billable_seconds: 900,
    auto_stop_minutes: 10,
    managed: true,
    adopted: false,
    ...extra,
  };
}

function compute(extra: Partial<CloudComputeStatus> = {}): CloudComputeStatus {
  return {
    state: "ready",
    ai: "ready",
    ai_label: "AI Ready",
    message: "AI готов.",
    error_code: null,
    detail: null,
    managed: true,
    adopted: false,
    session: session(),
    last_session: null,
    idle_deadline: null,
    ...extra,
  };
}

describe("policy money", () => {
  it("prints the policy defaults the way a user sets them", () => {
    // A new user starts on $0.52/hour and a $3.00 session budget.
    expect(policyMoney(0.52)).toBe("$0.52");
    expect(policyMoney(3)).toBe("$3.00");
    // The backend serializes money as a decimal string and keeps its precision there.
    expect(policyMoney("0.520000")).toBe("$0.52");
    expect(policyMoney("3.000000")).toBe("$3.00");
    expect(policyMoney(12.5)).toBe("$12.50");
  });

  it("says nothing it cannot read instead of inventing a value", () => {
    expect(policyMoney(null)).toBe("—");
    expect(policyMoney(undefined)).toBe("—");
    expect(policyMoney("")).toBe("—");
    expect(policyMoney("unknown")).toBe("—");
  });
});

describe("shared compute above the user's own limit", () => {
  it("names both rates when the running Pod costs more than this user's limit", () => {
    expect(overLimitRateLine(runningSharedSession(compute()), 0.52)).toBe(
      "Сейчас работает общий GPU за $0.79/час — это выше вашего предела $0.52/час. Можно повысить предел в настройках ниже.",
    );
  });

  it("counts a starting, loading, ready, generating and stopping Pod as running", () => {
    for (const state of [
      "starting_pod",
      "loading_model",
      "ready",
      "generating",
      "stopping",
    ]) {
      expect(runningSharedSession(compute({ state }))).not.toBeNull();
    }
  });

  it("never presents a planned or a finished Pod as running", () => {
    for (const state of [
      "offline",
      "searching",
      "gpu_found",
      "creating",
      "stopped",
      "create_unknown",
      "error",
    ]) {
      const planned = compute({ state });
      expect(runningSharedSession(planned)).toBeNull();
      expect(overLimitRateLine(runningSharedSession(planned), 0.52)).toBeNull();
    }
    expect(runningSharedSession(null)).toBeNull();
  });

  it("rounds both rates to the cents a user sees", () => {
    expect(
      overLimitRateLine(session({ hourly_rate_usd: "0.529000" }), "0.500000"),
    ).toBe(
      "Сейчас работает общий GPU за $0.53/час — это выше вашего предела $0.50/час. Можно повысить предел в настройках ниже.",
    );
  });

  it("stays silent when the Pod fits the policy", () => {
    expect(overLimitRateLine(session(), 0.79)).toBeNull();
    expect(overLimitRateLine(session(), 1)).toBeNull();
    expect(overLimitRateLine(session(), 100)).toBeNull();
  });

  it("stays silent when the rate or the limit is not known", () => {
    expect(overLimitRateLine(null, 0.52)).toBeNull();
    expect(
      overLimitRateLine(session({ hourly_rate_usd: "unknown" }), 0.52),
    ).toBeNull();
    expect(overLimitRateLine(session(), null)).toBeNull();
    expect(overLimitRateLine(session(), "")).toBeNull();
    expect(overLimitRateLine(session(), 0)).toBeNull();
  });
});
