import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TgBoardTab } from "../tg-board-tab";
import { TaskStatus, Team } from "@/types";

const { tasks } = vi.hoisted(() => ({
  tasks: { current: [] as Array<Record<string, unknown>> },
}));
vi.mock("@/hooks/use-tasks", () => ({
  useTasks: () => ({ data: tasks.current, isLoading: false }),
}));
vi.mock("@/components/tg/tg-task-sheet", () => ({
  TgTaskSheet: () => null,
}));

function task(overrides: Record<string, unknown>) {
  return {
    team: Team.BACKEND,
    assigned_to: null,
    updated_at: "2026-07-19T00:00:00Z",
    ...overrides,
  };
}

describe("TgBoardTab", () => {
  it("groups tasks by lifecycle stage and collapses done by default", async () => {
    tasks.current = [
      task({ id: "t1", title: "Blocked task", status: TaskStatus.BLOCKED }),
      task({ id: "t2", title: "QA task", status: TaskStatus.AWAITING_QA }),
      task({ id: "t3", title: "Flight task", status: TaskStatus.IN_PROGRESS }),
      task({ id: "t4", title: "Queued task", status: TaskStatus.PENDING }),
      task({
        id: "t5",
        title: "Done task A",
        status: TaskStatus.COMPLETED,
        updated_at: "2026-07-19T02:00:00Z",
      }),
      task({
        id: "t6",
        title: "Done task B",
        status: TaskStatus.COMPLETED,
        updated_at: "2026-07-19T01:00:00Z",
      }),
      task({
        id: "t7",
        title: "Cancelled task",
        status: TaskStatus.CANCELLED,
        updated_at: "2026-07-19T00:30:00Z",
      }),
    ];

    render(<TgBoardTab />);

    // Every non-empty group renders its section + its task.
    expect(screen.getByText("Needs you")).toBeInTheDocument();
    expect(screen.getByText("Blocked task")).toBeInTheDocument();
    expect(screen.getByText("In review")).toBeInTheDocument();
    expect(screen.getByText("QA task")).toBeInTheDocument();
    expect(screen.getByText("In flight")).toBeInTheDocument();
    expect(screen.getByText("Flight task")).toBeInTheDocument();
    expect(screen.getByText("Queued")).toBeInTheDocument();
    expect(screen.getByText("Queued task")).toBeInTheDocument();

    // Done is collapsed to a tally — no task titles rendered yet.
    expect(screen.getByText("Done")).toBeInTheDocument();
    expect(screen.getByText(/2 completed · 1 cancelled/)).toBeInTheDocument();
    expect(screen.queryByText("Done task A")).not.toBeInTheDocument();

    // Expanding reveals the recent terminal tasks.
    await userEvent.click(screen.getByText(/2 completed · 1 cancelled/));
    expect(screen.getByText("Done task A")).toBeInTheDocument();
    expect(screen.getByText("Cancelled task")).toBeInTheDocument();
  });

  it("shows a friendly empty state with no tasks", () => {
    tasks.current = [];
    render(<TgBoardTab />);
    expect(screen.getByText(/no tasks yet/i)).toBeInTheDocument();
  });
});
