import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AgentRole, AgentState, type Agent } from "@/types";
import type { MemberScorecard } from "@/types";

const { mockOrg, mockCeo, mockMember, mockAgents } = vi.hoisted(() => ({
  mockOrg: vi.fn(),
  mockCeo: vi.fn(),
  mockMember: vi.fn(),
  mockAgents: vi.fn(),
}));

vi.mock("@/hooks/use-observability", () => ({
  useOrgScorecard: mockOrg,
  useCeoScorecard: mockCeo,
  useMemberScorecard: mockMember,
}));

vi.mock("@/hooks/use-agents", () => ({
  useAgents: mockAgents,
}));

import { ScorecardsTabContent } from "../scorecards-tab";

function agent(id: string, name: string, role: AgentRole): Agent {
  return {
    id,
    agent_id: id,
    name,
    role,
    team: null,
    cell: null,
    status: AgentState.IDLE,
  };
}

function member(over: Partial<MemberScorecard>): MemberScorecard {
  return {
    scope: "member",
    id: "a1",
    name: "a1",
    member_kind: "agent",
    tasks_completed: 0,
    first_pass_yield: null,
    effort_throughput_per_hour: null,
    active_runtime_hours: 0,
    turns: 0,
    tool_calls: 0,
    tokens: 0,
    cost_usd: 0,
    turns_per_task: null,
    tool_calls_per_task: null,
    revisions_caused: 0,
    revisions_received: 0,
    qa_pass_rate: null,
    escalations: 0,
    blocked_others: 0,
    idle_hours: 0,
    utilization: null,
    includes_live_inflight: false,
    ...over,
  };
}

describe("ScorecardsTabContent", () => {
  beforeEach(() => {
    mockOrg.mockReturnValue({
      data: {
        scope: "org",
        team: null,
        member_count: 3,
        tasks_completed: 42,
        first_pass_yield: 0.75,
        effort_throughput_per_hour: 1.5,
        active_runtime_hours: 12.3,
        turns: 0,
        tool_calls: 0,
        tokens: 0,
        cost_usd: 9.5,
        revisions_caused: 0,
        revisions_received: 0,
      },
      isLoading: false,
    });
    mockCeo.mockReturnValue({
      data: {
        member_kind: "ceo",
        approval_p50_seconds: 3600,
        approval_p90_seconds: 7200,
        approval_count: 5,
        unblock_p50_seconds: 1800,
        unblock_count: 2,
        godmode_actions: 1,
      },
      isLoading: false,
    });
    mockMember.mockReturnValue({
      data: member({
        id: "dev1",
        name: "be-dev-1",
        tasks_completed: 7,
        first_pass_yield: 0.8,
        active_runtime_hours: 4.2,
        qa_pass_rate: 0.9,
        escalations: 1,
        blocked_others: 2,
        utilization: 0.6,
      }),
      isLoading: false,
    });
    mockAgents.mockReturnValue({
      data: [
        agent("dev1", "be-dev-1", AgentRole.DEVELOPER),
        agent("ceo", "Renzo", AgentRole.CEO),
        agent("sys", "system", AgentRole.SYSTEM),
      ],
    });
  });

  it("renders the org rollup headline figures", () => {
    render(<ScorecardsTabContent />);
    expect(screen.getByText("42")).toBeInTheDocument(); // tasks_completed
    expect(screen.getByText("75%")).toBeInTheDocument(); // first-pass yield
    expect(screen.getByText("$9.50")).toBeInTheDocument(); // cost
  });

  it("renders the CEO approval/unblock figures", () => {
    render(<ScorecardsTabContent />);
    expect(screen.getByText("Approvals")).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument(); // approval_count
    expect(screen.getByText("God-mode actions")).toBeInTheDocument();
  });

  it("lists only non-CEO/non-system members in the table", () => {
    render(<ScorecardsTabContent />);
    expect(screen.getByText("be-dev-1")).toBeInTheDocument();
    // CEO and system are excluded from the member table.
    expect(screen.queryByText("Renzo")).not.toBeInTheDocument();
    expect(screen.queryByText("system")).not.toBeInTheDocument();
  });

  it("explains an abbreviated member-table column via a hover tooltip", async () => {
    const user = userEvent.setup();
    render(<ScorecardsTabContent />);
    await user.hover(screen.getByText("FPY"));
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      /first-pass yield/i,
    );
  });

  it("surfaces load errors instead of an endless skeleton", () => {
    mockOrg.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
    });
    mockMember.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
    });
    render(<ScorecardsTabContent />);
    expect(
      screen.getByText(/failed to load organization metrics/i),
    ).toBeInTheDocument();
    // The member row shows a failed marker rather than a perpetual skeleton
    // (exact lowercase text, distinct from the org card's message).
    expect(screen.getByText("failed to load")).toBeInTheDocument();
  });
});
