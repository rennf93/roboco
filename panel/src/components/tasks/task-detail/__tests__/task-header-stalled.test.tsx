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

const { mockStalled } = vi.hoisted(() => ({ mockStalled: vi.fn() }));
vi.mock("@/hooks/use-dashboard", () => ({
  useStalledTasks: mockStalled,
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
  it("shows the stalled chip with the backend's reason when this task is in the stalled set (populated)", async () => {
    const user = userEvent.setup();
    mockStalled.mockReturnValue({
      data: [
        {
          id: "t1",
          title: "Some task",
          assigned_to: null,
          status: "blocked",
          blocker_resolver_type: "human",
          updated_at: "2026-08-01T00:00:00Z",
        },
      ],
    });

    render(<TaskHeader task={buildTask()} onAction={vi.fn()} />);

    const chip = screen.getByText("stalled");
    expect(chip).toBeInTheDocument();
    await user.hover(chip);
    expect(await screen.findByRole("tooltip")).toHaveTextContent("human");
  });

  it("hides the stalled chip when the stalled set is empty", () => {
    mockStalled.mockReturnValue({ data: [] });

    render(<TaskHeader task={buildTask()} onAction={vi.fn()} />);

    expect(screen.queryByText("stalled")).not.toBeInTheDocument();
  });

  it("hides the stalled chip (rather than fabricating stalled status) when the fetch errors", () => {
    mockStalled.mockReturnValue({ data: undefined, isError: true });

    render(<TaskHeader task={buildTask()} onAction={vi.fn()} />);

    expect(screen.queryByText("stalled")).not.toBeInTheDocument();
  });

  it("does not show the chip for a different task's stalled entry", () => {
    mockStalled.mockReturnValue({
      data: [
        {
          id: "some-other-task",
          title: "Other",
          assigned_to: null,
          status: "blocked",
          blocker_resolver_type: "human",
          updated_at: "2026-08-01T00:00:00Z",
        },
      ],
    });

    render(<TaskHeader task={buildTask()} onAction={vi.fn()} />);

    expect(screen.queryByText("stalled")).not.toBeInTheDocument();
  });
});