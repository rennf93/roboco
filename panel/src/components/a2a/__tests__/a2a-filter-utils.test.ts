import { describe, it, expect } from "vitest";
import type { AdminConversationSummary, AdminPairSummary } from "@/lib/api/a2a";
import {
  EMPTY_A2A_FILTERS,
  activeA2AFilterCount,
  distinctA2AAgents,
  filterConversations,
  filterPairs,
  type A2AFilters,
} from "../a2a-filter-utils";

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

function filters(overrides: Partial<A2AFilters> = {}): A2AFilters {
  return { ...EMPTY_A2A_FILTERS, ...overrides };
}

describe("filterConversations", () => {
  it("passes everything through with no active filters", () => {
    const conversations = [
      buildConversation({ status: "active" }),
      buildConversation({ id: "conv-2", status: "archived" }),
    ];
    expect(filterConversations(conversations, filters())).toHaveLength(2);
  });

  it("narrows by selected agents (either participant matches)", () => {
    const conversations = [
      buildConversation({
        id: "conv-1",
        agent_a: "be-dev-1",
        agent_b: "be-qa",
      }),
      buildConversation({
        id: "conv-2",
        agent_a: "ux-dev-1",
        agent_b: "ux-qa",
      }),
    ];
    const result = filterConversations(
      conversations,
      filters({ agents: ["be-qa"] }),
    );
    expect(result.map((c) => c.id)).toEqual(["conv-1"]);
  });

  it("narrows by task id fragment (case-insensitive)", () => {
    const conversations = [
      buildConversation({ id: "conv-1", task_id: "abcdef01-0000" }),
      buildConversation({ id: "conv-2", task_id: "ffffffff-0000" }),
    ];
    const result = filterConversations(
      conversations,
      filters({ taskIdFragment: "ABCDEF" }),
    );
    expect(result.map((c) => c.id)).toEqual(["conv-1"]);
  });

  it("narrows to task_id === null when noLinkedTask is set", () => {
    const conversations = [
      buildConversation({ id: "conv-1", task_id: null }),
      buildConversation({ id: "conv-2", task_id: "abcdef01-0000" }),
    ];
    const result = filterConversations(
      conversations,
      filters({ noLinkedTask: true }),
    );
    expect(result.map((c) => c.id)).toEqual(["conv-1"]);
  });

  it("ORs the task fragment and no-linked-task toggle when both are set", () => {
    const conversations = [
      buildConversation({ id: "conv-1", task_id: null }),
      buildConversation({ id: "conv-2", task_id: "abcdef01-0000" }),
      buildConversation({ id: "conv-3", task_id: "zzzzzzzz-0000" }),
    ];
    const result = filterConversations(
      conversations,
      filters({ taskIdFragment: "abcdef", noLinkedTask: true }),
    );
    expect(result.map((c) => c.id).sort()).toEqual(["conv-1", "conv-2"]);
  });

  it("narrows by selected statuses", () => {
    const conversations = [
      buildConversation({ id: "conv-1", status: "active" }),
      buildConversation({ id: "conv-2", status: "archived" }),
    ];
    const result = filterConversations(
      conversations,
      filters({ statuses: ["archived"] }),
    );
    expect(result.map((c) => c.id)).toEqual(["conv-2"]);
  });

  it("narrows by date range on last_message_at at day granularity", () => {
    const conversations = [
      buildConversation({
        id: "conv-1",
        last_message_at: "2026-07-02T12:00:00Z",
      }),
      buildConversation({
        id: "conv-2",
        last_message_at: "2026-07-05T12:00:00Z",
      }),
    ];
    const result = filterConversations(
      conversations,
      filters({ dateFrom: "2026-07-03", dateTo: "2026-07-06" }),
    );
    expect(result.map((c) => c.id)).toEqual(["conv-2"]);
  });

  it("falls back to created_at for the date range when last_message_at is null", () => {
    const conversations = [
      buildConversation({
        id: "conv-1",
        last_message_at: null,
        created_at: "2026-07-02T12:00:00Z",
      }),
    ];
    expect(
      filterConversations(
        conversations,
        filters({ dateFrom: "2026-07-02", dateTo: "2026-07-02" }),
      ),
    ).toHaveLength(1);
    expect(
      filterConversations(conversations, filters({ dateFrom: "2026-07-03" })),
    ).toHaveLength(0);
  });
});

describe("filterPairs", () => {
  it("passes everything through with no active filters", () => {
    const pairs = [
      buildPair({ conversation_id: "conv-1" }),
      buildPair({ agent_a: "auditor", agent_b: "product-owner" }),
    ];
    expect(filterPairs(pairs, filters())).toHaveLength(2);
  });

  it("narrows by selected agents only — Task/Status/Date never apply", () => {
    const pairs = [
      buildPair({ agent_a: "be-dev-1", agent_b: "be-qa" }),
      buildPair({ agent_a: "auditor", agent_b: "product-owner" }),
    ];
    const result = filterPairs(
      pairs,
      filters({
        agents: ["be-dev-1"],
        statuses: ["archived"],
        dateFrom: "2099-01-01",
      }),
    );
    expect(result).toHaveLength(1);
    expect(result[0].agent_a).toBe("be-dev-1");
  });
});

describe("distinctA2AAgents", () => {
  it("dedupes and sorts agent slugs across conversations and pairs", () => {
    const conversations = [
      buildConversation({ agent_a: "fe-qa", agent_b: "be-qa" }),
    ];
    const pairs = [buildPair({ agent_a: "be-dev-1", agent_b: "be-qa" })];
    expect(distinctA2AAgents(conversations, pairs)).toEqual([
      "be-dev-1",
      "be-qa",
      "fe-qa",
    ]);
  });
});

describe("activeA2AFilterCount", () => {
  it("counts zero for the empty filter state", () => {
    expect(activeA2AFilterCount(EMPTY_A2A_FILTERS)).toBe(0);
  });

  it("counts one entry per active chip", () => {
    expect(
      activeA2AFilterCount(
        filters({
          agents: ["be-dev-1", "be-qa"],
          statuses: ["active"],
          dateFrom: "2026-07-01",
        }),
      ),
    ).toBe(4);
  });
});
