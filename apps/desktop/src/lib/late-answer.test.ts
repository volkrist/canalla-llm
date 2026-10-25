import { describe, expect, it } from "vitest";
import { awaitsLateAnswer } from "./late-answer";

describe("late answer watch", () => {
  it("watches a task that is still working after its stream is gone", () => {
    for (const status of [
      "READY",
      "INSPECTING",
      "PLANNING",
      "EXECUTING",
      "VERIFYING",
      "RECOVERING",
      "WAITING_LLM",
      "WAITING_WORKSPACE",
      "WAITING_DEVICE",
      "WAITING_CONFIRMATION",
      "PAUSED",
    ]) {
      expect(awaitsLateAnswer(status), status).toBe(true);
    }
  });

  it("stops watching terminal states", () => {
    for (const status of ["COMPLETED", "FAILED", "STOPPED"]) {
      expect(awaitsLateAnswer(status), status).toBe(false);
    }
  });

  it("treats a missing status as nothing to watch", () => {
    expect(awaitsLateAnswer(null)).toBe(false);
    expect(awaitsLateAnswer(undefined)).toBe(false);
    expect(awaitsLateAnswer("")).toBe(false);
  });
});
