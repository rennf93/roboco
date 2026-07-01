import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

const { mockOrg } = vi.hoisted(() => ({ mockOrg: vi.fn() }));

vi.mock("@/hooks/use-observability", () => ({
  useOrgScorecard: mockOrg,
}));

import { ScorecardOverviewPanel } from "../scorecard-overview-panel";

describe("ScorecardOverviewPanel", () => {
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
  });

  it("renders headline org figures", () => {
    render(<ScorecardOverviewPanel />);
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("75%")).toBeInTheDocument();
    expect(screen.getByText("$9.50")).toBeInTheDocument();
  });

  it("deep-links into the Scorecards metrics tab", () => {
    render(<ScorecardOverviewPanel />);
    const link = screen.getByRole("link", { name: /scorecards/i });
    expect(link).toHaveAttribute("href", "/metrics?tab=scorecards");
  });

  it("shows a skeleton while loading", () => {
    mockOrg.mockReturnValue({ data: undefined, isLoading: true });
    const { container } = render(<ScorecardOverviewPanel />);
    expect(container.querySelectorAll('[data-slot="skeleton"]').length).toBe(5);
  });
});
