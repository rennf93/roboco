import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { TaskStatus, Team, TaskType, type Task } from "@/types";
import { useScrollRestorationStore } from "@/lib/stores";

const mockPush = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, back: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

import { TaskListNav } from "../task-list-nav";

function buildTask(overrides: Partial<Task> = {}): Task {
  return {
    id: "t2",
    title: "Task 2",
    description: "d",
    status: TaskStatus.IN_PROGRESS,
    team: Team.BACKEND,
    task_type: TaskType.CODE,
    acceptance_criteria: [],
    parent_task_id: null,
    ...overrides,
  } as unknown as Task;
}

describe("TaskListNav", () => {
  beforeEach(() => {
    useScrollRestorationStore.setState({ taskListNav: null });
    mockPush.mockClear();
  });

  it("disables both buttons with an explanatory tooltip when no list context exists", () => {
    render(<TaskListNav task={buildTask()} />);
    expect(screen.getByLabelText("Previous task")).toBeDisabled();
    expect(screen.getByLabelText("Next task")).toBeDisabled();
  });

  it("disables both buttons when the current task isn't part of the captured list order", () => {
    useScrollRestorationStore.setState({
      taskListNav: {
        items: [
          { id: "other-1", title: "Other 1" },
          { id: "other-2", title: "Other 2" },
        ],
        queryString: "",
      },
    });
    render(<TaskListNav task={buildTask()} />);
    expect(screen.getByLabelText("Previous task")).toBeDisabled();
    expect(screen.getByLabelText("Next task")).toBeDisabled();
  });

  it("links prev/next to the adjacent tasks in the captured list order", () => {
    useScrollRestorationStore.setState({
      taskListNav: {
        items: [
          { id: "t1", title: "Task 1" },
          { id: "t2", title: "Task 2" },
          { id: "t3", title: "Task 3" },
        ],
        queryString: "status=in_progress",
      },
    });
    render(<TaskListNav task={buildTask({ id: "t2" })} />);

    const prev = screen.getByLabelText("Previous task");
    const next = screen.getByLabelText("Next task");
    expect(prev).not.toBeDisabled();
    expect(next).not.toBeDisabled();
    expect(prev.closest("a")).toHaveAttribute(
      "href",
      "/tasks/t1?status=in_progress",
    );
    expect(next.closest("a")).toHaveAttribute(
      "href",
      "/tasks/t3?status=in_progress",
    );
  });

  it("disables prev at the start of the list and next at the end", () => {
    useScrollRestorationStore.setState({
      taskListNav: {
        items: [
          { id: "t1", title: "Task 1" },
          { id: "t2", title: "Task 2" },
        ],
        queryString: "",
      },
    });
    render(<TaskListNav task={buildTask({ id: "t1" })} />);
    expect(screen.getByLabelText("Previous task")).toBeDisabled();
    expect(screen.getByLabelText("Next task")).not.toBeDisabled();
  });

  it("navigates to the next task on Alt+ArrowRight", () => {
    useScrollRestorationStore.setState({
      taskListNav: {
        items: [
          { id: "t1", title: "Task 1" },
          { id: "t2", title: "Task 2" },
          { id: "t3", title: "Task 3" },
        ],
        queryString: "status=in_progress",
      },
    });
    render(<TaskListNav task={buildTask({ id: "t2" })} />);

    fireEvent.keyDown(window, { key: "ArrowRight", altKey: true });

    expect(mockPush).toHaveBeenCalledWith(
      "/tasks/t3?status=in_progress",
    );
  });

  it("suppresses Alt+ArrowRight while an input is focused", () => {
    useScrollRestorationStore.setState({
      taskListNav: {
        items: [
          { id: "t1", title: "Task 1" },
          { id: "t2", title: "Task 2" },
          { id: "t3", title: "Task 3" },
        ],
        queryString: "",
      },
    });
    render(
      <>
        <input aria-label="Note field" />
        <TaskListNav task={buildTask({ id: "t2" })} />
      </>,
    );

    const input = screen.getByLabelText("Note field");
    input.focus();
    fireEvent.keyDown(input, { key: "ArrowRight", altKey: true });

    expect(mockPush).not.toHaveBeenCalled();
  });
});
