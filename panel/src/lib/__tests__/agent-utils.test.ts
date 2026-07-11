import { describe, it, expect } from "vitest";
import {
  resolveToSlug,
  getAgentDisplayName,
  getAgentInitials,
  getAgentTeamColor,
  isKnownAgent,
  registerAgentRoster,
  TEAM_COLOR_CLASSES,
  type AgentTeamColor,
} from "@/lib/agent-utils";

// Canonical UUIDs from the backend roster (roboco/foundation/identity.py).
const PR_REVIEWER_UUID = "00000000-0000-0000-0004-000000000007";
const SECRETARY_UUID = "00000000-0000-0000-0004-000000000006";
const INTAKE_UUID = "00000000-0000-0000-0004-000000000005";
const BE_DEV_1_UUID = "00000000-0000-0000-0001-000000000001";

describe("agent-utils board-adjacent agents", () => {
  // Regression: these three agents exist in the backend roster but were
  // missing from the panel's static maps, so a task assigned to one rendered
  // as a truncated raw UUID instead of the agent's name.
  it("resolves the pr-reviewer UUID to its slug and name", () => {
    expect(resolveToSlug(PR_REVIEWER_UUID)).toBe("pr-reviewer-1");
    expect(getAgentDisplayName(PR_REVIEWER_UUID)).toBe("PR Reviewer");
    expect(isKnownAgent("pr-reviewer-1")).toBe(true);
  });

  it("resolves the secretary UUID to its slug and name", () => {
    expect(resolveToSlug(SECRETARY_UUID)).toBe("secretary-1");
    expect(getAgentDisplayName(SECRETARY_UUID)).toBe("Secretary");
    expect(isKnownAgent("secretary-1")).toBe(true);
  });

  it("resolves the intake UUID to its slug and name", () => {
    expect(resolveToSlug(INTAKE_UUID)).toBe("intake-1");
    expect(getAgentDisplayName(INTAKE_UUID)).toBe("Intake");
    expect(isKnownAgent("intake-1")).toBe(true);
  });

  it("never falls back to a truncated UUID for a seeded agent", () => {
    // The bug symptom: an unresolved UUID returns its first 8 chars.
    for (const uuid of [PR_REVIEWER_UUID, SECRETARY_UUID, INTAKE_UUID]) {
      expect(getAgentDisplayName(uuid)).not.toBe(uuid.slice(0, 8));
    }
  });

  it("gives each new agent a 3-letter code (not a generic fallback)", () => {
    expect(getAgentInitials(PR_REVIEWER_UUID)).toBe("PRR");
    expect(getAgentInitials(SECRETARY_UUID)).toBe("SEC");
    expect(getAgentInitials(INTAKE_UUID)).toBe("INT");
  });
});

describe("agent-utils live roster (drift-proofing)", () => {
  // The static map is only a fallback. Once the live /api/agents roster is
  // registered, ANY agent the backend knows about resolves — including ones
  // added after this file was written, so the panel can never drift again.
  it("resolves an agent that exists only in the live roster", () => {
    const FUTURE_UUID = "00000000-0000-0000-0009-000000000001";
    // Not resolvable before registration — falls back to the truncated UUID.
    expect(getAgentDisplayName(FUTURE_UUID)).toBe("00000000");

    registerAgentRoster([
      { uuid: FUTURE_UUID, slug: "future-agent-1", name: "Future Agent" },
    ]);

    expect(resolveToSlug(FUTURE_UUID)).toBe("future-agent-1");
    expect(getAgentDisplayName(FUTURE_UUID)).toBe("Future Agent");
    expect(getAgentDisplayName("future-agent-1")).toBe("Future Agent");
    expect(isKnownAgent("future-agent-1")).toBe(true);
  });

  it("lets the live roster override a stale static name", () => {
    const PR_REVIEWER_UUID = "00000000-0000-0000-0004-000000000007";
    registerAgentRoster([
      { uuid: PR_REVIEWER_UUID, slug: "pr-reviewer-1", name: "Code Reviewer" },
    ]);
    expect(getAgentDisplayName(PR_REVIEWER_UUID)).toBe("Code Reviewer");
  });
});

describe("agent-utils existing roster (regression guard)", () => {
  it("still resolves a cell agent", () => {
    expect(resolveToSlug(BE_DEV_1_UUID)).toBe("be-dev-1");
    expect(getAgentDisplayName(BE_DEV_1_UUID)).toBe("Backend Dev 1");
  });

  it("returns Unassigned for null", () => {
    expect(getAgentDisplayName(null)).toBe("Unassigned");
  });
});

describe("getAgentTeamColor (design doc §2 — six buckets)", () => {
  it.each([
    ["be-dev-1", "backend"],
    ["be-pm", "backend"],
    ["fe-dev-2", "frontend"],
    ["fe-qa", "frontend"],
    ["ux-dev-1", "ux_ui"],
    ["ux-doc", "ux_ui"],
    ["main-pm", "board"],
    ["product-owner", "board"],
    ["head-marketing", "board"],
    ["auditor", "board"],
    ["ceo", "ceo"],
    ["CEO", "ceo"],
    ["intake-1", "system"],
    ["secretary-1", "system"],
    ["pr-reviewer-1", "system"],
  ] satisfies [string, AgentTeamColor][])(
    "buckets %s as %s",
    (slug, expected) => {
      expect(getAgentTeamColor(slug)).toBe(expected);
    },
  );

  it("resolves a UUID to its team bucket via the slug map", () => {
    expect(getAgentTeamColor(BE_DEV_1_UUID)).toBe("backend");
  });

  it("falls back to system for an unrecognized id, never throwing", () => {
    expect(getAgentTeamColor("some-unknown-agent")).toBe("system");
    expect(getAgentTeamColor(null)).toBe("system");
  });

  it("gives every bucket a class string with no new color families beyond the six", () => {
    const buckets: AgentTeamColor[] = [
      "backend",
      "frontend",
      "ux_ui",
      "board",
      "ceo",
      "system",
    ];
    for (const bucket of buckets) {
      expect(TEAM_COLOR_CLASSES[bucket]).toBeTruthy();
    }
  });
});
