import { describe, it, expect } from "vitest";
import type { AdminConversationSummary, AdminPairSummary } from "@/lib/api/a2a";
import { filterConversations, filterPairs } from "../a2a-filter-utils";

function buildConversation(
  overrides: Partial<AdminConversationSummary> = {},
): AdminConversationSummary {
  return {
    id: "conv-1",
    agent_a: "be-dev-1",
    agent_b: "be-qa",
    topic: "QA handoff",
    task_id: null,
    status: "active",
    message_count: 3,
    last_message_at: "2026-07-02T09:00:00Z",
    last_message_preview: null,
    created_at: "2026-07-01T08:00:00Z",
    updated_at: "2026-07-02T09:00:00Z",
    ...overrides,
  };
}

function buildPair(
  overrides: Partial<AdminPairSummary> = {},
): AdminPairSummary {
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

describe("filterConversations", () => {
  it("passes everything through with an empty search and status=all", () => {
    const conversations = [
      buildConversation({ status: "active" }),
      buildConversation({ id: "conv-2", status: "closed" }),
    ];
    expect(filterConversations(conversations, "all", "")).toHaveLength(2);
  });

  it("narrows to active-status conversations when status=active", () => {
    const conversations = [
      buildConversation({ id: "conv-1", status: "active" }),
      buildConversation({ id: "conv-2", status: "closed" }),
    ];
    const result = filterConversations(conversations, "active", "");
    expect(result.map((c) => c.id)).toEqual(["conv-1"]);
  });

  it("matches search against agent display name (case-insensitive)", () => {
    const conversations = [buildConversation()];
    expect(
      filterConversations(conversations, "all", "backend qa"),
    ).toHaveLength(1);
    expect(filterConversations(conversations, "all", "nope")).toHaveLength(0);
  });

  it("matches search against the topic", () => {
    const conversations = [buildConversation({ topic: "QA handoff" })];
    expect(filterConversations(conversations, "all", "handoff")).toHaveLength(
      1,
    );
  });

  it("matches search against a raw agent slug", () => {
    const conversations = [buildConversation({ agent_a: "be-dev-1" })];
    expect(filterConversations(conversations, "all", "be-dev-1")).toHaveLength(
      1,
    );
  });
});

describe("filterPairs", () => {
  it("passes everything through with an empty search and status=all", () => {
    const pairs = [
      buildPair({ conversation_id: "conv-1" }),
      buildPair({ agent_a: "auditor", agent_b: "product-owner" }),
    ];
    expect(filterPairs(pairs, "all", "")).toHaveLength(2);
  });

  it("narrows to pairs with a conversation when status=active", () => {
    const pairs = [
      buildPair({ conversation_id: "conv-1" }),
      buildPair({ agent_a: "auditor", agent_b: "product-owner" }),
    ];
    const result = filterPairs(pairs, "active", "");
    expect(result).toHaveLength(1);
    expect(result[0].conversation_id).toBe("conv-1");
  });

  it("matches search against agent display name (case-insensitive)", () => {
    const pairs = [buildPair()];
    expect(filterPairs(pairs, "all", "backend dev 1")).toHaveLength(1);
    expect(filterPairs(pairs, "all", "nope")).toHaveLength(0);
  });
});
