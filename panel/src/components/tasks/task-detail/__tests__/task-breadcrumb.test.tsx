import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { TaskStatus, Team, TaskType, type Task } from "@/types";

const mockUseTask = vi.fn();
vi.mock("@/hooks/use-tasks", () => ({
  useTask: (id: string) => mockUseTask(id),
}));

import { TaskBreadcrumb } from "../task-breadcrumb";

function buildTask(overrides: Partial<Task> = {}): Task {
  return {
    id: "child-1",
    title: "Child task",
    description: "d",
    status: TaskStatus.IN_PROGRESS,
    team: Team.BACKEND,
    task_type: TaskType.CODE,
    acceptance_criteria: [],
    parent_task_id: null,
    ...overrides,
  } as unknown as Task;
}

describe("TaskBreadcrumb", () => {
  it("renders nothing when the task has no parent", () => {
    mockUseTask.mockReturnValue({ data: undefined, isLoading: false });
    const { container } = render(
      <TaskBreadcrumb task={buildTask({ parent_task_id: null })} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders a link to the parent task when one exists", () => {
    mockUseTask.mockReturnValue({
      data: { id: "parent-1", title: "Parent task" },
      isLoading: false,
    });
    render(
      <TaskBreadcrumb
        task={buildTask({ parent_task_id: "parent-1" })}
      />,
    );
    const link = screen.getByRole("link", { name: "Parent task" });
    expect(link).toHaveAttribute("href", "/tasks/parent-1");
    expect(screen.getByText("Child task")).toBeInTheDocument();
  });
});
