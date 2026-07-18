import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { TaskStatus, Team, TaskType, type Task } from "@/types";

const { useTasks } = vi.hoisted(() => ({ useTasks: vi.fn() }));
vi.mock("@/hooks/use-tasks", () => ({ useTasks }));

import { MobileTaskBoard } from "../mobile-task-board";

function buildTask(overrides: Partial<Task> = {}): Task {
  return {
    id: "t1",
    title: "Fix the thing",
    description: "",
    status: TaskStatus.IN_PROGRESS,
    team: Team.BACKEND,
    task_type: TaskType.CODE,
    acceptance_criteria: [],
    parent_task_id: null,
    assigned_to: "be-dev-1",
    ...overrides,
  } as unknown as Task;
}

describe("MobileTaskBoard", () => {
  beforeEach(() => {
    useTasks.mockReset();
  });

  it("renders skeletons while loading", () => {
    useTasks.mockReturnValue({ data: undefined, isLoading: true });
    const { container } = render(<MobileTaskBoard />);
    expect(container.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0);
  });

  it("shows an empty state when there are no tasks", () => {
    useTasks.mockReturnValue({ data: [], isLoading: false });
    render(<MobileTaskBoard />);
    expect(screen.getByText("No tasks")).toBeInTheDocument();
  });

  it("groups tasks into per-status collapsible sections with title/assignee rows", () => {
    useTasks.mockReturnValue({
      data: [
        buildTask({ id: "a", title: "In progress task", status: TaskStatus.IN_PROGRESS, assigned_to: "be-dev-1" }),
        buildTask({ id: "b", title: "Another in-progress task", status: TaskStatus.IN_PROGRESS, assigned_to: "be-dev-2" }),
        buildTask({ id: "c", title: "Done task", status: TaskStatus.COMPLETED, assigned_to: null }),
      ],
      isLoading: false,
    });
    render(<MobileTaskBoard />);

    // in_progress is open-by-default: its 2 rows are immediately visible.
    expect(screen.getByText("In progress task")).toBeInTheDocument();
    expect(screen.getByText("Another in-progress task")).toBeInTheDocument();
    expect(screen.getByText("Backend Dev 1")).toBeInTheDocument();
    expect(screen.getByText("Backend Dev 2")).toBeInTheDocument();
    // Section header text is split across nested spans ("in progress" + a
    // separately-styled "(2)"), so query by the trigger button's accessible
    // name (which aggregates descendant text) rather than getByText, which
    // doesn't match text broken up across multiple elements.
    expect(
      screen.getByRole("button", { name: /in progress \(2\)/i }),
    ).toBeInTheDocument();

    // completed is collapsed by default: the section header shows, the row doesn't.
    expect(
      screen.getByRole("button", { name: /completed \(1\)/i }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Done task")).not.toBeInTheDocument();

    // expanding it reveals the row and its "Unassigned" fallback.
    fireEvent.click(screen.getByRole("button", { name: /completed \(1\)/i }));
    expect(screen.getByText("Done task")).toBeInTheDocument();
    expect(screen.getByText("Unassigned")).toBeInTheDocument();
  });
});
