import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

const { mockSeries, mockSessions, mockJournals } = vi.hoisted(() => ({
  mockSeries: vi.fn(),
  mockSessions: vi.fn(),
  mockJournals: vi.fn(),
}));

vi.mock("@/hooks/use-usage", () => ({
  useUsageTimeSeries: mockSeries,
}));
vi.mock("@/hooks/use-work-sessions", () => ({
  useWorkSessions: mockSessions,
}));
vi.mock("@/hooks/use-journals", () => ({
  useAgentJournalEntries: mockJournals,
}));

import { AgentActivityPanel } from "../agent-activity-panel";

describe("AgentActivityPanel", () => {
  beforeEach(() => {
    mockSeries.mockReturnValue({ data: [], isLoading: false });
    mockSessions.mockReturnValue({ data: [], isLoading: false });
    mockJournals.mockReturnValue({ data: [], isLoading: false });
  });

  it("renders both card titles", () => {
    render(
      <AgentActivityPanel agentSlug="be-dev-1" agentUuid="uuid-1" />,
    );
    expect(screen.getByText("Token Activity")).toBeInTheDocument();
    expect(screen.getByText("Recent Activity")).toBeInTheDocument();
  });

  it("shows empty states when there is no history", () => {
    render(
      <AgentActivityPanel agentSlug="be-dev-1" agentUuid="uuid-1" />,
    );
    expect(
      screen.getByText("No token usage in the last 7 days"),
    ).toBeInTheDocument();
    expect(screen.getByText("No recent activity")).toBeInTheDocument();
  });

  it("does not show the empty state while loading", () => {
    mockSeries.mockReturnValue({ data: [], isLoading: true });
    mockSessions.mockReturnValue({ data: [], isLoading: true });
    render(
      <AgentActivityPanel agentSlug="be-dev-1" agentUuid="uuid-1" />,
    );
    expect(
      screen.queryByText("No token usage in the last 7 days"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("No recent activity"),
    ).not.toBeInTheDocument();
  });

  it("renders a work-session timeline entry", () => {
    mockSessions.mockReturnValue({
      data: [
        {
          id: "s1",
          task_id: "abcdef12-3456-7890",
          branch_name: "feature/backend/ABC12345",
          status: "completed",
          started_at: "2026-07-14T10:00:00Z",
          has_pr: true,
        },
      ],
      isLoading: false,
    });
    render(
      <AgentActivityPanel agentSlug="be-dev-1" agentUuid="uuid-1" />,
    );
    expect(screen.getByText("Task abcdef12")).toBeInTheDocument();
  });
});