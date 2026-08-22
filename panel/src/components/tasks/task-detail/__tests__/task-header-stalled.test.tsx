import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TaskStatus, Team, TaskType, type Task } from "@/types";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), back: vi.fn() }),
}));

vi.mock("@/hooks/use-tasks", () => ({
  useUpdateTask: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteTask: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useTaskValidTransitions: () => ({ data: [], isLoading: false }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { TaskHeader } from "../task-header";

function buildTask(overrides: Partial<Task> = {}): Task {
  return {
    id: "t1",
    title: "Some task",
    description: "d",
    status: TaskStatus.IN_PROGRESS,
    team: Team.BACKEND,
    task_type: TaskType.CODE,
    acceptance_criteria: [],
    ...overrides,
  } as unknown as Task;
}

describe("TaskHeader stalled indicator", () => {
  it("shows the stalled chip with the task's own stalled_reason (populated)", async () => {
    const user = userEvent.setup();

    render(
      <TaskHeader
        task={buildTask({ stalled_reason: "respawn breaker gave up" })}
        onAction={vi.fn()}
      />,
    );

    const chip = screen.getByText("stalled");
    expect(chip).toBeInTheDocument();
    await user.hover(chip);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "respawn breaker gave up",
    );
  });

  it("hides the stalled chip when stalled_reason is null", () => {
    render(
      <TaskHeader
        task={buildTask({ stalled_reason: null })}
        onAction={vi.fn()}
      />,
    );

    expect(screen.queryByText("stalled")).not.toBeInTheDocument();
  });

  it("hides the stalled chip when stalled_reason is absent", () => {
    render(<TaskHeader task={buildTask()} onAction={vi.fn()} />);

    expect(screen.queryByText("stalled")).not.toBeInTheDocument();
  });
});
