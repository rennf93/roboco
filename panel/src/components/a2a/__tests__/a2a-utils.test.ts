import { describe, it, expect } from "vitest";
import {
  connectionDotClasses,
  connectionStateLabel,
  lastSenderOf,
  pickDefaultRecipient,
  recipientOptions,
} from "../a2a-utils";
import type { ConnectionState } from "@/lib/websocket/connection";

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

describe("connectionStateLabel (design doc §3 — all four states distinct)", () => {
  it.each([
    ["connected", "Live"],
    ["connecting", "Connecting…"],
    ["reconnecting", "Reconnecting…"],
    ["disconnected", "Offline"],
  ] satisfies [ConnectionState, string][])(
    "labels %s as %s",
    (state, label) => {
      expect(connectionStateLabel(state)).toBe(label);
    },
  );
});

describe("connectionDotClasses", () => {
  it("connected is static — no pulse", () => {
    expect(connectionDotClasses("connected")).not.toContain("animate-pulse");
  });

  it("connecting and reconnecting pulse with a motion-reduce guard", () => {
    for (const state of ["connecting", "reconnecting"] as ConnectionState[]) {
      const classes = connectionDotClasses(state);
      expect(classes).toContain("animate-pulse");
      expect(classes).toContain("motion-reduce:animate-none");
    }
  });

  it("disconnected is static and muted", () => {
    const classes = connectionDotClasses("disconnected");
    expect(classes).not.toContain("animate-pulse");
    expect(classes).toContain("muted-foreground");
  });
});
