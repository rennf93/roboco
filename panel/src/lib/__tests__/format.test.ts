import { describe, it, expect } from "vitest";
import { formatTokens, formatBucket, bucketGranularity } from "@/lib/format";

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

describe("bucketGranularity", () => {
  it("hourly when buckets sit ~1h apart", () => {
    expect(
      bucketGranularity([
        "2026-07-15T00:00:00Z",
        "2026-07-15T01:00:00Z",
        "2026-07-15T02:00:00Z",
      ]),
    ).toBe("hour");
  });

  it("daily when buckets sit ~24h apart", () => {
    expect(
      bucketGranularity([
        "2026-07-13T00:00:00Z",
        "2026-07-14T00:00:00Z",
        "2026-07-15T00:00:00Z",
      ]),
    ).toBe("day");
  });

  it("defaults to hourly with too few points to tell", () => {
    expect(bucketGranularity(["2026-07-15T00:00:00Z"])).toBe("hour");
  });
});

describe("formatBucket", () => {
  it("renders a daily bucket as a date, never a bare local hour", () => {
    // Regression: a midnight-UTC daily bucket used to render as the viewer's
    // local hour ("02:00" at UTC+2) for every tick. It must be a date.
    const out = formatBucket("2026-07-15T00:00:00.000Z", "day");
    expect(out).not.toMatch(/^\d{2}:00$/);
    expect(out).toContain("15");
  });

  it("renders an hourly bucket as HH:00", () => {
    const bucket = "2026-07-15T14:00:00.000Z";
    const expectedHour = new Date(bucket)
      .getHours()
      .toString()
      .padStart(2, "0");
    expect(formatBucket(bucket, "hour")).toBe(`${expectedHour}:00`);
  });
});
