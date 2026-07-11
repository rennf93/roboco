import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { TaskStatus, Team, TaskType, type Task } from "@/types";

// tooltip-aria-label-spec.md §1a: the move-forward button already carried a
// conditional `title` (disabled-vs-enabled); it needs a matching aria-label
// using the identical text, per §2's "same string for both" rule.

vi.mock("@/hooks/use-tasks", () => ({
  useUpdateTask: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

import { KanbanCard } from "../kanban-card";

function buildTask(overrides: Partial<Task> = {}): Task {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    title: "A task",
    description: "",
    acceptance_criteria: [],
    status: TaskStatus.IN_PROGRESS,
    priority: 2,
    sequence: null,
    team: Team.BACKEND,
    assigned_to: null,
    task_type: TaskType.CODE,
    ...overrides,
  } as unknown as Task;
}

describe("KanbanCard — move-forward aria-label (tooltip-aria-label-spec §1a)", () => {
  it("uses 'Move forward' as both aria-label and title when the task is actionable", () => {
    render(
      <KanbanCard
        task={buildTask()}
        onAction={vi.fn()}
        showQaActions={false}
      />,
    );

    const button = screen.getByRole("button", { name: "Move forward" });
    expect(button).toHaveAttribute("title", "Move forward");
    expect(button).not.toBeDisabled();
  });

  it("swaps to the disabled-reason text on both aria-label and title for a backlog task", () => {
    render(
      <KanbanCard
        task={buildTask({ status: TaskStatus.BACKLOG })}
        onAction={vi.fn()}
        showQaActions={false}
      />,
    );

    const button = screen.getByRole("button", {
      name: "PM must activate this task first",
    });
    expect(button).toHaveAttribute("title", "PM must activate this task first");
    expect(button).toBeDisabled();
  });

  it("gives the drag handle an accessible name", () => {
    render(
      <KanbanCard
        task={buildTask()}
        onAction={vi.fn()}
        showQaActions={false}
      />,
    );

    expect(
      screen.getByLabelText("Drag to move task between columns"),
    ).toBeInTheDocument();
  });
});
