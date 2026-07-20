import { describe, it, expect } from "vitest";
import { formatTokens, formatBucket } from "@/lib/format";

describe("formatTokens", () => {
  it("renders raw digits below 1000", () => {
    expect(formatTokens(0)).toBe("0");
    expect(formatTokens(999)).toBe("999");
  });

  it("renders K with one decimal at the 1000 boundary", () => {
    expect(formatTokens(1_000)).toBe("1.0K");
    expect(formatTokens(22_596)).toBe("22.6K");
    expect(formatTokens(999_999)).toBe("1000.0K");
  });

  it("renders M with two decimals at the 1,000,000 boundary", () => {
    expect(formatTokens(1_000_000)).toBe("1.00M");
    expect(formatTokens(22_596_000)).toBe("22.60M");
  });
});

describe("formatBucket", () => {
  it("formats a midnight-UTC daily bucket as MM/DD", () => {
    // Regression guard: a plain "T00:00:00.000Z" bucket must never be
    // mistaken for an hourly bucket just because minutes/seconds are 0.
    expect(formatBucket("2026-07-15T00:00:00.000Z")).toBe(
      new Date("2026-07-15T00:00:00.000Z").getMonth() +
        1 +
        "/" +
        new Date("2026-07-15T00:00:00.000Z").getDate(),
    );
  });

  it("formats a non-midnight hourly bucket as HH:00", () => {
    const bucket = "2026-07-15T14:00:00.000Z";
    const expectedHour = new Date(bucket).getHours().toString().padStart(2, "0");
    expect(formatBucket(bucket)).toBe(`${expectedHour}:00`);
  });
});
