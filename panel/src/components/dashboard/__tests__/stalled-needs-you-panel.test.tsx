import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";

const { mockStalled, mockAgents } = vi.hoisted(() => ({
  mockStalled: vi.fn(),
  mockAgents: vi.fn(),
}));

vi.mock("@/hooks/use-dashboard", () => ({
  useStalledTasks: mockStalled,
}));

vi.mock("@/hooks/use-agents", () => ({
  useAgents: mockAgents,
}));

import { StalledNeedsYouPanel } from "../stalled-needs-you-panel";

describe("StalledNeedsYouPanel", () => {
  beforeEach(() => {
    vi.useFakeTimers({ now: new Date("2026-08-10T02:00:00Z") });
    mockAgents.mockReturnValue({
      data: [{ id: "agent-1", name: "FE-Dev-1" }],
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders each stalled task's title, assignee, status, reason, and duration, linking to task detail", () => {
    mockStalled.mockReturnValue({
      data: [
        {
          id: "task-abc",
          title: "Fix the flaky test",
          assigned_to: "agent-1",
          status: "blocked",
          blocker_resolver_type: "human",
          updated_at: "2026-08-10T00:00:00Z",
        },
      ],
      isLoading: false,
      isError: false,
    });

    render(<StalledNeedsYouPanel />);

    expect(screen.getByText("Fix the flaky test")).toBeInTheDocument();
    expect(screen.getByText("FE-Dev-1")).toBeInTheDocument();
    expect(screen.getByText("blocked")).toBeInTheDocument();
    expect(screen.getByText("human")).toBeInTheDocument();
    expect(screen.getByText(/stalled for 2h/i)).toBeInTheDocument();
    const link = screen.getByRole("link");
    expect(link).toHaveAttribute("href", "/tasks/task-abc");
  });

  it("renders an explicit empty state when nothing is stalled", () => {
    mockStalled.mockReturnValue({ data: [], isLoading: false, isError: false });

    render(<StalledNeedsYouPanel />);

    expect(screen.getByText(/nothing is stalled/i)).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("renders a distinct error state when the fetch fails, never the empty state", () => {
    mockStalled.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
    });

    render(<StalledNeedsYouPanel />);

    expect(
      screen.getByText(/failed to load stalled tasks/i),
    ).toBeInTheDocument();
    expect(screen.queryByText(/nothing is stalled/i)).not.toBeInTheDocument();
  });
});