import { describe, it, expect } from "vitest";
import {
  exceedsReadabilityThreshold,
  READABILITY_LINE_THRESHOLD,
  READABILITY_CHAR_THRESHOLD,
} from "@/lib/content-readability";

describe("exceedsReadabilityThreshold", () => {
  it("is false for empty or short content", () => {
    expect(exceedsReadabilityThreshold("")).toBe(false);
    expect(exceedsReadabilityThreshold("A single short line.")).toBe(false);
  });

  it("is true once the line count exceeds the threshold", () => {
    const lines = Array(READABILITY_LINE_THRESHOLD + 1)
      .fill("x")
      .join("\n");
    expect(exceedsReadabilityThreshold(lines)).toBe(true);
  });

  it("is true once the character count exceeds the threshold", () => {
    const long = "a".repeat(READABILITY_CHAR_THRESHOLD + 1);
    expect(exceedsReadabilityThreshold(long)).toBe(true);
  });

  it("is false right at the thresholds", () => {
    const atLineLimit = Array(READABILITY_LINE_THRESHOLD).fill("x").join("\n");
    expect(exceedsReadabilityThreshold(atLineLimit)).toBe(false);
    const atCharLimit = "a".repeat(READABILITY_CHAR_THRESHOLD);
    expect(exceedsReadabilityThreshold(atCharLimit)).toBe(false);
  });
});
