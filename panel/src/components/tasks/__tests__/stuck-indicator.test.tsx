import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TaskStatus, type Task } from "@/types";
import { StuckIndicator } from "../stuck-indicator";

// The backend slice ships in parallel — contract fields are mocked here, per
// the task brief. Only the fields this component reads are set.
function baseTask(overrides: Partial<Task> = {}): Task {
  return {
    id: "task-1",
    title: "Task",
    description: "",
    acceptance_criteria: [],
    status: TaskStatus.IN_PROGRESS,
    priority: 2,
    sequence: 0,
    team: "frontend" as Task["team"],
    created_by: "ceo",
    assigned_to: null,
    parent_task_id: null,
    dependency_ids: [],
    blocker_ids: [],
    created_at: "2026-01-01T00:00:00Z",
    updated_at: null,
    claimed_at: null,
    started_at: null,
    completed_at: null,
    target_date: null,
    estimated_complexity: "medium" as Task["estimated_complexity"],
    nature: "technical" as Task["nature"],
    task_type: "code" as Task["task_type"],
    project_id: null,
    docs_complete: false,
    pr_created: false,
    pm_approvals: {},
    plan: null,
    checkpoints: [],
    progress_updates: [],
    commits: [],
    dev_notes: null,
    qa_notes: null,
    auditor_notes: null,
    quick_context: null,
    self_verified: false,
    qa_verified: null,
    branch_name: null,
    pr_number: null,
    pr_url: null,
    ...overrides,
  };
}

describe("StuckIndicator", () => {
  it("renders nothing on a healthy task", () => {
    const { container } = render(<StuckIndicator task={baseTask()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows active time since progress when wedge strikes are open", () => {
    render(
      <StuckIndicator
        task={baseTask({
          active_time_since_progress: "2h 5m",
          open_wedge_strikes: 2,
          wedge_cycle: [
            { actor: "main-pm", verb: "respawn", timestamp: "2026-09-27T10:00:00Z" },
          ],
        })}
      />,
    );

    const chip = screen.getByTestId("stuck-indicator");
    expect(chip).toHaveTextContent("stuck · 2h 5m since progress");
  });

  it("renders without the time segment when no active time is present", () => {
    render(
      <StuckIndicator task={baseTask({ open_wedge_strikes: 1 })} />,
    );
    expect(screen.getByTestId("stuck-indicator")).toHaveTextContent(/^stuck$/);
  });

  it("shows stuck for a blocked-for-human task without strikes", () => {
    render(
      <StuckIndicator
        task={baseTask({
          status: TaskStatus.BLOCKED,
          blocker_resolver_type: "human",
        })}
      />,
    );
    expect(screen.getByTestId("stuck-indicator")).toBeInTheDocument();
  });

  it("does not show for a blocked-for-agent task", () => {
    const { container } = render(
      <StuckIndicator
        task={baseTask({
          status: TaskStatus.BLOCKED,
          blocker_resolver_type: "agent",
        })}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("tooltip lists the wedge cycle and states it is read-only", async () => {
    const user = userEvent.setup();
    render(
      <StuckIndicator
        task={baseTask({
          active_time_since_progress: 5400,
          open_wedge_strikes: 1,
          wedge_cycle: [
            { actor: "main-pm", verb: "respawn", timestamp: "2026-09-27T10:00:00Z" },
          ],
        })}
      />,
    );

    await user.hover(screen.getByTestId("stuck-indicator"));
    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip).toHaveTextContent("1 open wedge strike");
    expect(tooltip).toHaveTextContent("main-pm → respawn");
    expect(tooltip).toHaveTextContent("Read-only");
  });
});
