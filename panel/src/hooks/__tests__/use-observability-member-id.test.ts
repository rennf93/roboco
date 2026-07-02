import { describe, expect, it } from "vitest";
import { isScorecardMemberId } from "../use-observability";

// The static fallback roster (use-agents AGENT_ROSTER) carries placeholder
// ids "1".."22" while agent definitions load. Fetching scorecards for those
// fired 22 guaranteed-422 requests per refetch cycle, clogging the browser's
// connection pool and delaying every metrics tab by ~10s.
describe("isScorecardMemberId", () => {
  it("rejects the static fallback roster placeholder ids", () => {
    for (let i = 1; i <= 22; i++) {
      expect(isScorecardMemberId(String(i))).toBe(false);
    }
  });

  it("accepts real agent UUIDs", () => {
    expect(isScorecardMemberId("00000000-0000-0000-0002-000000000001")).toBe(
      true,
    );
    expect(isScorecardMemberId("A1B2C3D4-E5F6-7890-ABCD-EF0123456789")).toBe(
      true,
    );
  });

  it("accepts the ceo alias (the CEO card's member endpoint)", () => {
    expect(isScorecardMemberId("ceo")).toBe(true);
  });

  it("rejects empty and junk ids", () => {
    expect(isScorecardMemberId("")).toBe(false);
    expect(isScorecardMemberId("main-pm")).toBe(false);
    expect(isScorecardMemberId("not-a-uuid-at-all")).toBe(false);
  });
});
