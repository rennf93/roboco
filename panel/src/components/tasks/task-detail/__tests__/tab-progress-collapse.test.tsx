import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";
import React from "react";
import { TaskStatus, Team, TaskType, type Task } from "@/types";

// Regression: a task with a long progress history used to render every
// update fully expanded, forcing continuous scrolling. Only the 2 most
// recent progress updates default open; older ones default collapsed.

vi.mock("@/hooks/use-tasks", () => ({
  useUpdateTask: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { TabProgress } from "../tab-progress";

function buildTask(overrides: Partial<Task> = {}): Task {
  return {
    id: "t1",
    title: "Task",
    description: "d",
    status: TaskStatus.IN_PROGRESS,
    team: Team.BACKEND,
    task_type: TaskType.CODE,
    acceptance_criteria: [],
    checkpoints: [],
    progress_updates: [],
    ...overrides,
  } as unknown as Task;
}

function makeUpdates(count: number) {
  return Array.from({ length: count }, (_, i) => ({
    timestamp: new Date(2026, 0, 1 + i).toISOString(),
    agent_id: "be-dev-1",
    message: `Update number ${i}`,
    percentage: null,
  }));
}

describe("TabProgress progress-update collapse", () => {
  it("only the 2 most recent updates default expanded when there are many", () => {
    const task = buildTask({ progress_updates: makeUpdates(30) });
    const { container } = render(<TabProgress task={task} />);

    // Scope to the collapse trigger specifically via its test id — the row
    // also carries a delete button wrapped in a HelpTip/Tooltip, which
    // stamps its own Radix data-state, so a bare "button[data-state]"
    // selector would match both. List order matches sort order (newest
    // first), so the first two entries are the 2 most recent.
    const triggers = Array.from(
      container.querySelectorAll(
        'li button[data-testid="progress-update-trigger"]',
      ),
    );
    expect(triggers).toHaveLength(30);
    expect(triggers[0]).toHaveAttribute("data-state", "open");
    expect(triggers[1]).toHaveAttribute("data-state", "open");
    expect(triggers[2]).toHaveAttribute("data-state", "closed");
    expect(triggers[29]).toHaveAttribute("data-state", "closed");
  });
});
