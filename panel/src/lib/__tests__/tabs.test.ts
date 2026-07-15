import { describe, expect, it } from "vitest";
import { pickTab } from "@/lib/tabs";

const VIEWS = ["dev", "qa", "pr-review", "pm"] as const;

describe("pickTab", () => {
  it("returns the raw value when it is in the valid set", () => {
    expect(pickTab("qa", VIEWS, "dev")).toBe("qa");
  });

  it("falls back when raw is null", () => {
    expect(pickTab(null, VIEWS, "dev")).toBe("dev");
  });

  it("falls back when raw is an invalid/typo value", () => {
    expect(pickTab("deev", VIEWS, "dev")).toBe("dev");
    expect(pickTab("foo", VIEWS, "dev")).toBe("dev");
  });

  it("falls back when raw is the empty string", () => {
    expect(pickTab("", VIEWS, "dev")).toBe("dev");
  });
});