import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

const { mockStalled } = vi.hoisted(() => ({
  mockStalled: vi.fn(),
}));

vi.mock("@/hooks/use-dashboard", () => ({
  useStalledTasks: mockStalled,
}));

import { StalledNeedsYouPanel } from "../stalled-needs-you-panel";

describe("StalledNeedsYouPanel", () => {
  it("renders each stalled task's title, assignee, status, reason, and duration, linking to task detail", () => {
    mockStalled.mockReturnValue({
      data: [
        {
          task_id: "task-abc",
          title: "Fix the flaky test",
          assignee_id: "agent-1",
          assignee_slug: "fe-dev-1",
          status: "in_progress",
          reason: "respawn breaker gave up after 4 strikes",
          stalled_since: "2026-08-10T00:00:00Z",
          stalled_seconds: 7200,
        },
      ],
      isLoading: false,
      isError: false,
    });

    render(<StalledNeedsYouPanel />);

    expect(screen.getByText("Fix the flaky test")).toBeInTheDocument();
    expect(screen.getByText("fe-dev-1")).toBeInTheDocument();
    expect(screen.getByText("in_progress")).toBeInTheDocument();
    expect(
      screen.getByText("respawn breaker gave up after 4 strikes"),
    ).toBeInTheDocument();
    expect(screen.getByText(/stalled for 2h/i)).toBeInTheDocument();
    const link = screen.getByRole("link");
    expect(link).toHaveAttribute("href", "/tasks/task-abc");
  });

  it("falls back to assignee_id when assignee_slug is null", () => {
    mockStalled.mockReturnValue({
      data: [
        {
          task_id: "task-abc",
          title: "Fix the flaky test",
          assignee_id: "agent-1",
          assignee_slug: null,
          status: "blocked",
          reason: "waiting on human",
          stalled_since: "2026-08-10T00:00:00Z",
          stalled_seconds: 60,
        },
      ],
      isLoading: false,
      isError: false,
    });

    render(<StalledNeedsYouPanel />);

    expect(screen.getByText("agent-1")).toBeInTheDocument();
    expect(screen.getByText(/stalled for < 1h/i)).toBeInTheDocument();
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
