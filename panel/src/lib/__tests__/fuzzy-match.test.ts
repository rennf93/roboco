import { describe, it, expect } from "vitest";
import { fuzzyScore, fuzzyMatch } from "@/lib/fuzzy-match";

describe("fuzzyScore", () => {
  it("matches an empty query against anything with the best score", () => {
    expect(fuzzyScore("", "anything")).toBe(0);
  });

  it("scores an exact substring match by its start index", () => {
    expect(fuzzyScore("dev", "fe-dev-2")).toBe("fe-dev-2".indexOf("dev"));
    expect(fuzzyScore("fe-dev", "fe-dev-2")).toBe(0);
  });

  it("is case-insensitive", () => {
    expect(fuzzyScore("DEV", "fe-dev-2")).not.toBeNull();
  });

  it("matches a non-contiguous subsequence and penalizes larger gaps", () => {
    const tight = fuzzyScore("cmp", "command palette");
    const loose = fuzzyScore("cmp", "c a a m a a p");
    expect(tight).not.toBeNull();
    expect(loose).not.toBeNull();
    expect(tight as number).toBeLessThan(loose as number);
  });

  it("returns null when the query is not a subsequence of the target", () => {
    expect(fuzzyScore("xyz", "command palette")).toBeNull();
  });
});

describe("fuzzyMatch", () => {
  it("is true for a subsequence match and false otherwise", () => {
    expect(fuzzyMatch("cp", "command palette")).toBe(true);
    expect(fuzzyMatch("zzz", "command palette")).toBe(false);
  });
});
