import { describe, it, expect } from "vitest";
import {
  lastSenderOf,
  pickDefaultRecipient,
  recipientOptions,
} from "../a2a-utils";

describe("lastSenderOf", () => {
  it("returns null for an empty transcript", () => {
    expect(lastSenderOf([])).toBeNull();
  });

  it("returns the chronologically latest sender even when payload is unordered", () => {
    expect(
      lastSenderOf([
        { from_agent: "be-qa", created_at: "2026-07-02T10:05:00Z" },
        { from_agent: "be-dev-1", created_at: "2026-07-02T10:00:00Z" },
      ]),
    ).toBe("be-qa");
  });
});

describe("pickDefaultRecipient", () => {
  it("picks the last sender when they are a participant", () => {
    expect(pickDefaultRecipient("be-dev-1", "be-qa", "be-qa")).toBe("be-qa");
  });

  it("falls back to agent_a when the transcript is empty", () => {
    expect(pickDefaultRecipient("be-dev-1", "be-qa", null)).toBe("be-dev-1");
  });

  it("falls back to agent_a when the last sender is not a participant", () => {
    expect(pickDefaultRecipient("be-dev-1", "be-qa", "fe-dev-1")).toBe(
      "be-dev-1",
    );
  });
});

describe("recipientOptions", () => {
  it("returns both participants when neither is the CEO", () => {
    expect(recipientOptions("be-dev-1", "be-qa")).toEqual([
      "be-dev-1",
      "be-qa",
    ]);
  });

  it("excludes the CEO when it's agent_a", () => {
    expect(recipientOptions("ceo", "be-dev-1")).toEqual(["be-dev-1"]);
  });

  it("excludes the CEO when it's agent_b", () => {
    expect(recipientOptions("be-dev-1", "ceo")).toEqual(["be-dev-1"]);
  });
});
