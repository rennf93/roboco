import { describe, it, expect, beforeEach } from "vitest";
import { useRateLimitStore } from "@/store/rate-limit-store";
import type {
  RateLimitHitEvent,
  RateLimitLiftedEvent,
  RateLimitApiResponse,
  RateLimitEntry,
} from "@/types/rate-limits";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const TIMESTAMP = "2026-06-20T12:00:00.000Z";

function makeHitEvent(
  provider = "anthropic",
  retryAfterSeconds = 60,
  affectedAgents: string[] = [],
): RateLimitHitEvent {
  return {
    type: "RATE_LIMIT_HIT",
    provider,
    affectedAgents,
    retryAfterSeconds,
    timestamp: TIMESTAMP,
  };
}

function makeLiftedEvent(provider = "anthropic"): RateLimitLiftedEvent {
  return {
    type: "RATE_LIMIT_LIFTED",
    provider,
    timestamp: TIMESTAMP,
  };
}

function makeApiEntry(
  provider: string,
  hitAt = TIMESTAMP,
  retryAfterSeconds = 30,
): RateLimitEntry {
  return {
    provider,
    affectedAgents: [],
    hitAt,
    resumeAt: new Date(
      new Date(hitAt).getTime() + retryAfterSeconds * 1000,
    ).toISOString(),
    retryAfterSeconds,
  };
}

// ---------------------------------------------------------------------------
// Reset store before every test
// ---------------------------------------------------------------------------

beforeEach(() => {
  useRateLimitStore.setState({ limits: new Map() });
});

// ---------------------------------------------------------------------------
// hitRateLimit
// ---------------------------------------------------------------------------

describe("useRateLimitStore — hitRateLimit", () => {
  it("creates an entry keyed by provider", () => {
    useRateLimitStore.getState().hitRateLimit(makeHitEvent("anthropic", 60));
    const limits = useRateLimitStore.getState().limits;
    expect(limits.has("anthropic")).toBe(true);
  });

  it("stores the correct provider on the entry", () => {
    useRateLimitStore.getState().hitRateLimit(makeHitEvent("openai", 30));
    const entry = useRateLimitStore.getState().limits.get("openai");
    expect(entry?.provider).toBe("openai");
  });

  it("stores affectedAgents on the entry", () => {
    const agents = ["be-dev-1", "be-dev-2"];
    useRateLimitStore
      .getState()
      .hitRateLimit(makeHitEvent("anthropic", 60, agents));
    const entry = useRateLimitStore.getState().limits.get("anthropic");
    expect(entry?.affectedAgents).toEqual(agents);
  });

  it("sets hitAt equal to the event timestamp", () => {
    useRateLimitStore.getState().hitRateLimit(makeHitEvent("anthropic", 60));
    const entry = useRateLimitStore.getState().limits.get("anthropic");
    expect(entry?.hitAt).toBe(TIMESTAMP);
  });

  it("sets retryAfterSeconds equal to the event value", () => {
    useRateLimitStore.getState().hitRateLimit(makeHitEvent("anthropic", 45));
    const entry = useRateLimitStore.getState().limits.get("anthropic");
    expect(entry?.retryAfterSeconds).toBe(45);
  });

  it("computes resumeAt as timestamp + retryAfterSeconds * 1000 ms", () => {
    const retryAfterSeconds = 60;
    useRateLimitStore
      .getState()
      .hitRateLimit(makeHitEvent("anthropic", retryAfterSeconds));
    const entry = useRateLimitStore.getState().limits.get("anthropic");

    const expectedResumeAt = new Date(
      new Date(TIMESTAMP).getTime() + retryAfterSeconds * 1000,
    ).toISOString();
    expect(entry?.resumeAt).toBe(expectedResumeAt);
  });

  it("allows multiple providers to coexist in the map", () => {
    useRateLimitStore.getState().hitRateLimit(makeHitEvent("anthropic", 60));
    useRateLimitStore.getState().hitRateLimit(makeHitEvent("openai", 30));
    const limits = useRateLimitStore.getState().limits;
    expect(limits.size).toBe(2);
    expect(limits.has("anthropic")).toBe(true);
    expect(limits.has("openai")).toBe(true);
  });

  it("overwrites an existing entry for the same provider", () => {
    useRateLimitStore.getState().hitRateLimit(makeHitEvent("anthropic", 60));
    useRateLimitStore.getState().hitRateLimit(makeHitEvent("anthropic", 120));
    const limits = useRateLimitStore.getState().limits;
    expect(limits.size).toBe(1);
    expect(limits.get("anthropic")?.retryAfterSeconds).toBe(120);
  });
});

// ---------------------------------------------------------------------------
// liftRateLimit
// ---------------------------------------------------------------------------

describe("useRateLimitStore — liftRateLimit", () => {
  it("deletes the entry for the specified provider", () => {
    useRateLimitStore.getState().hitRateLimit(makeHitEvent("anthropic", 60));
    expect(useRateLimitStore.getState().limits.has("anthropic")).toBe(true);

    useRateLimitStore.getState().liftRateLimit(makeLiftedEvent("anthropic"));
    expect(useRateLimitStore.getState().limits.has("anthropic")).toBe(false);
  });

  it("does not remove entries for other providers", () => {
    useRateLimitStore.getState().hitRateLimit(makeHitEvent("anthropic", 60));
    useRateLimitStore.getState().hitRateLimit(makeHitEvent("openai", 30));

    useRateLimitStore.getState().liftRateLimit(makeLiftedEvent("anthropic"));
    expect(useRateLimitStore.getState().limits.has("openai")).toBe(true);
    expect(useRateLimitStore.getState().limits.size).toBe(1);
  });

  it("is a no-op when the provider is not in the map", () => {
    // Should not throw
    expect(() =>
      useRateLimitStore
        .getState()
        .liftRateLimit(makeLiftedEvent("non-existent")),
    ).not.toThrow();
    expect(useRateLimitStore.getState().limits.size).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// syncFromApi
// ---------------------------------------------------------------------------

describe("useRateLimitStore — syncFromApi", () => {
  // M44: syncFromApi merges by freshest hitAt instead of wholesale-replacing,
  // so an out-of-order (older) API snapshot can't regress a fresher WS hit.
  it("merges API entries into the map, keeping existing entries not in the response", () => {
    // Pre-populate with a WS hit on a provider the API snapshot omits.
    useRateLimitStore.getState().hitRateLimit(makeHitEvent("old-provider", 60));

    const response: RateLimitApiResponse = {
      entries: [makeApiEntry("anthropic"), makeApiEntry("openai")],
    };
    useRateLimitStore.getState().syncFromApi(response);

    const limits = useRateLimitStore.getState().limits;
    // Pre-existing entry not in the response is retained (merge, not replace).
    expect(limits.has("old-provider")).toBe(true);
    // New API entries are present.
    expect(limits.has("anthropic")).toBe(true);
    expect(limits.has("openai")).toBe(true);
    expect(limits.size).toBe(3);
  });

  it("correctly keys entries by provider name", () => {
    const entry = makeApiEntry("anthropic", TIMESTAMP, 45);
    useRateLimitStore.getState().syncFromApi({ entries: [entry] });
    const stored = useRateLimitStore.getState().limits.get("anthropic");
    expect(stored).toEqual(entry);
  });

  it("keeps existing limits when the API returns an empty entries array", () => {
    // An empty snapshot is a no-op merge, not a clear — a stale/empty poll
    // must not wipe a fresher WS-derived map.
    useRateLimitStore.getState().hitRateLimit(makeHitEvent("anthropic", 60));
    useRateLimitStore.getState().syncFromApi({ entries: [] });
    expect(useRateLimitStore.getState().limits.size).toBe(1);
    expect(useRateLimitStore.getState().limits.has("anthropic")).toBe(true);
  });

  it("keeps the fresher hitAt when an out-of-order (older) API snapshot arrives", () => {
    // A recent WS HIT stamps anthropic at T2.
    const T2 = "2026-06-20T12:05:00.000Z";
    useRateLimitStore
      .getState()
      .hitRateLimit(makeHitEvent("anthropic", 60, ["be-dev-1"]));
    // Manually advance hitAt to T2 to simulate a fresher WS event.
    const fresher = useRateLimitStore.getState().limits.get("anthropic")!;
    useRateLimitStore.setState({
      limits: new Map([["anthropic", { ...fresher, hitAt: T2 }]]),
    });

    // An API snapshot arrives carrying an older T1 for anthropic.
    const T1 = "2026-06-20T12:00:00.000Z";
    useRateLimitStore
      .getState()
      .syncFromApi({ entries: [makeApiEntry("anthropic", T1)] });

    const stored = useRateLimitStore.getState().limits.get("anthropic");
    expect(stored?.hitAt).toBe(T2);

    // A brand-new provider in the same snapshot is still added.
    useRateLimitStore.getState().syncFromApi({
      entries: [makeApiEntry("anthropic", T1), makeApiEntry("openai", T1)],
    });
    expect(useRateLimitStore.getState().limits.has("openai")).toBe(true);
    expect(useRateLimitStore.getState().limits.get("anthropic")?.hitAt).toBe(
      T2,
    );
  });

  it("overwrites a stale local entry when the API snapshot is fresher", () => {
    const T2 = "2026-06-20T12:05:00.000Z";
    // Local entry at TIMESTAMP, API arrives with T2 — API wins.
    useRateLimitStore.getState().hitRateLimit(makeHitEvent("anthropic", 60));
    useRateLimitStore
      .getState()
      .syncFromApi({ entries: [makeApiEntry("anthropic", T2)] });
    expect(useRateLimitStore.getState().limits.get("anthropic")?.hitAt).toBe(
      T2,
    );
  });
});
