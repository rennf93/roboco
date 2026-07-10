import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { TaskStatus, Team, TaskType, type Task } from "@/types";

function buildTask(overrides: Partial<Task> = {}): Task {
  return {
    id: "task-1",
    title: "Ship the context pane",
    description: "",
    acceptance_criteria: [],
    status: TaskStatus.IN_PROGRESS,
    priority: 1,
    project_id: "proj-1",
    task_type: TaskType.CODE,
    team: Team.FRONTEND,
    assigned_to: null,
    parent_task_id: null,
    branch_name: null,
    pr_number: null,
    pr_url: null,
    created_at: "2026-07-02T10:00:00Z",
    updated_at: "2026-07-02T10:00:00Z",
    ...overrides,
  } as Task;
}

let mockTask: Task | undefined;
let mockIsLoading = false;

vi.mock("@/hooks/use-tasks", () => ({
  useTask: () => ({ data: mockTask, isLoading: mockIsLoading }),
}));

import { A2AContextPane } from "../a2a-context-pane";

describe("A2AContextPane", () => {
  it("renders both participants' identity cards linking to /agents/{slug}", () => {
    mockTask = undefined;
    mockIsLoading = false;
    render(<A2AContextPane agentA="be-dev-1" agentB="be-qa" taskId={null} />);
    expect(screen.getByText("Backend Dev 1")).toBeInTheDocument();
    expect(screen.getByText("Backend QA")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Backend Dev 1/ })).toHaveAttribute(
      "href",
      "/agents/be-dev-1",
    );
  });

  it("shows the no-task hint when the conversation has no linked task", () => {
    mockTask = undefined;
    mockIsLoading = false;
    render(<A2AContextPane agentA="be-dev-1" agentB="be-qa" taskId={null} />);
    expect(
      screen.getByText("This conversation has no linked task"),
    ).toBeInTheDocument();
  });

  it("shows the linked task's title, status, and a View task link", () => {
    mockTask = buildTask({ title: "Ship the context pane" });
    mockIsLoading = false;
    render(<A2AContextPane agentA="be-dev-1" agentB="be-qa" taskId="task-1" />);
    expect(screen.getByText("Ship the context pane")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /View task/ })).toHaveAttribute(
      "href",
      "/tasks/task-1",
    );
  });
});
