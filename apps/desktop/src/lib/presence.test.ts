import { describe, expect, it } from "vitest";
import { lastSeen } from "../hooks/usePresence";
describe("last seen UTC formatting", () => {
  const at = Date.parse("2026-09-15T00:00:00Z");
  it.each([
    [0, "только что"],
    [120000, "2 мин назад"],
    [3600000, "1 ч назад"],
    [86400000, "вчера"],
  ])("formats %i", (delta, expected) => {
    expect(lastSeen(new Date(at - Number(delta)).toISOString(), at)).toBe(
      expected,
    );
  });
  it("handles unknown and future timestamps", () => {
    expect(lastSeen(null, at)).toBe("Ещё не был в сети");
    expect(lastSeen(new Date(at + 1000).toISOString(), at)).toBe("только что");
  });
});
