import { describe, it, expect } from "vitest";
import type { AdminPairSummary } from "@/lib/api/a2a";
import {
  pairKey,
  pairMatchesFrame,
  latestPulseTimestamps,
  groupPairsBySection,
  sortPairsForSection,
  SECTION_LABELS,
} from "../a2a-switchboard-utils";

function buildPair(overrides: Partial<AdminPairSummary> = {}): AdminPairSummary {
  return {
    agent_a: "be-dev-1",
    role_a: "developer",
    team_a: "backend",
    agent_b: "be-qa",
    role_b: "qa",
    team_b: "backend",
    group_key: "cell-backend",
    conversation_id: null,
    last_message_at: null,
    message_count: 0,
    ...overrides,
  };
}

describe("pairKey", () => {
  it("is order-independent", () => {
    expect(pairKey("be-dev-1", "be-qa")).toBe(pairKey("be-qa", "be-dev-1"));
  });

  it("always puts the lexically smaller slug first", () => {
    expect(pairKey("be-qa", "be-dev-1")).toBe("be-dev-1|be-qa");
  });
});

describe("pairMatchesFrame", () => {
  it("matches regardless of from/to direction", () => {
    expect(pairMatchesFrame("be-dev-1", "be-qa", "be-qa", "be-dev-1")).toBe(
      true,
    );
    expect(pairMatchesFrame("be-dev-1", "be-qa", "be-dev-1", "be-qa")).toBe(
      true,
    );
  });

  it("does not match a different pair", () => {
    expect(pairMatchesFrame("be-dev-1", "be-qa", "fe-dev-1", "fe-qa")).toBe(
      false,
    );
  });

  it("does not match when either side is missing", () => {
    expect(pairMatchesFrame("be-dev-1", "be-qa", undefined, "be-qa")).toBe(
      false,
    );
    expect(pairMatchesFrame("be-dev-1", "be-qa", "be-dev-1", null)).toBe(
      false,
    );
  });
});

describe("latestPulseTimestamps", () => {
  const pairs = [
    buildPair({ agent_a: "be-dev-1", agent_b: "be-qa" }),
    buildPair({
      agent_a: "fe-dev-1",
      agent_b: "fe-qa",
      group_key: "cell-frontend",
    }),
  ];

  it("returns the epoch ms of the matching frame for a pulsed pair", () => {
    const result = latestPulseTimestamps(
      [
        {
          from_agent: "be-dev-1",
          to_agent: "be-qa",
          timestamp: "2026-07-02T10:00:00Z",
        },
      ],
      pairs,
    );
    expect(result[pairKey("be-dev-1", "be-qa")]).toBe(
      new Date("2026-07-02T10:00:00Z").getTime(),
    );
  });

  it("omits pairs with no matching frame", () => {
    const result = latestPulseTimestamps(
      [
        {
          from_agent: "be-dev-1",
          to_agent: "be-qa",
          timestamp: "2026-07-02T10:00:00Z",
        },
      ],
      pairs,
    );
    expect(result[pairKey("fe-dev-1", "fe-qa")]).toBeUndefined();
  });

  it("keeps only the most recent matching frame per pair", () => {
    const result = latestPulseTimestamps(
      [
        {
          from_agent: "be-dev-1",
          to_agent: "be-qa",
          timestamp: "2026-07-02T09:00:00Z",
        },
        {
          from_agent: "be-qa",
          to_agent: "be-dev-1",
          timestamp: "2026-07-02T11:00:00Z",
        },
      ],
      pairs,
    );
    expect(result[pairKey("be-dev-1", "be-qa")]).toBe(
      new Date("2026-07-02T11:00:00Z").getTime(),
    );
  });

  it("ignores frames with an unparseable timestamp", () => {
    const result = latestPulseTimestamps(
      [{ from_agent: "be-dev-1", to_agent: "be-qa", timestamp: "not-a-date" }],
      pairs,
    );
    expect(result[pairKey("be-dev-1", "be-qa")]).toBeUndefined();
  });

  it("returns an empty map for an empty frame list", () => {
    expect(latestPulseTimestamps([], pairs)).toEqual({});
  });
});

describe("sortPairsForSection", () => {
  it("sorts pairs with history before never-talked pairs, most recent first", () => {
    const older = buildPair({
      agent_a: "be-dev-1",
      agent_b: "be-doc",
      conversation_id: "c1",
      last_message_at: "2026-07-01T00:00:00Z",
    });
    const newer = buildPair({
      agent_a: "be-dev-2",
      agent_b: "be-doc",
      conversation_id: "c2",
      last_message_at: "2026-07-02T00:00:00Z",
    });
    const neverTalked = buildPair({
      agent_a: "be-pm",
      agent_b: "be-qa",
      conversation_id: null,
      last_message_at: null,
    });

    const sorted = sortPairsForSection([older, neverTalked, newer]);
    expect(sorted.map((p) => p.conversation_id)).toEqual(["c2", "c1", null]);
  });

  it("breaks ties between never-talked pairs alphabetically", () => {
    const b = buildPair({ agent_a: "fe-dev-1", agent_b: "fe-qa" });
    const a = buildPair({ agent_a: "be-dev-1", agent_b: "be-qa" });
    const sorted = sortPairsForSection([b, a]);
    expect(sorted[0]).toBe(a);
    expect(sorted[1]).toBe(b);
  });
});

describe("groupPairsBySection", () => {
  it("groups pairs by group_key and orders sections canonically", () => {
    const pairs = [
      buildPair({ group_key: "cross", agent_a: "be-pm", agent_b: "fe-pm" }),
      buildPair({
        group_key: "board",
        agent_a: "auditor",
        agent_b: "product-owner",
      }),
      buildPair({ group_key: "cell-backend" }),
      buildPair({
        group_key: "pm-chain",
        agent_a: "be-pm",
        agent_b: "main-pm",
      }),
    ];

    const sections = groupPairsBySection(pairs);

    expect(sections.map((s) => s.groupKey)).toEqual([
      "cell-backend",
      "pm-chain",
      "board",
      "cross",
    ]);
    for (const section of sections) {
      expect(section.label).toBe(SECTION_LABELS[section.groupKey]);
      expect(section.pairs.length).toBe(1);
    }
  });

  it("appends unrecognized group keys after the canonical sections", () => {
    const pairs = [
      buildPair({ group_key: "cell-backend" }),
      buildPair({ group_key: "mystery-group" }),
    ];
    const sections = groupPairsBySection(pairs);
    expect(sections.map((s) => s.groupKey)).toEqual([
      "cell-backend",
      "mystery-group",
    ]);
    expect(sections[1].label).toBe("mystery-group");
  });

  it("returns no sections for an empty pair list", () => {
    expect(groupPairsBySection([])).toEqual([]);
  });
});
