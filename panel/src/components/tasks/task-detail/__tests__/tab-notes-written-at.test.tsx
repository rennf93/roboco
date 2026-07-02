import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";
import React from "react";
import { TaskStatus, Team, TaskType, type Task } from "@/types";

// Structured note sections are overwrite-in-place; apply_structured_note
// stamps written_at so the panel can show WHEN a trace landed (CEO
// reMarkable item: "Task notes and other traces: TIMESTAMPS!").

vi.mock("@/hooks/use-tasks", () => ({
  useUpdateTask: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { TabNotes } from "../tab-notes";

function buildTask(overrides: Partial<Task> = {}): Task {
  return {
    id: "t1",
    title: "Task",
    description: "d",
    status: TaskStatus.IN_PROGRESS,
    team: Team.BACKEND,
    task_type: TaskType.CODE,
    acceptance_criteria: [],
    ...overrides,
  } as unknown as Task;
}

describe("notes tab written_at stamps", () => {
  it("shows the section's written_at next to the card title", () => {
    const task = buildTask({
      dev_notes: "Built the greeting module.",
      notes_structured: {
        developer: {
          summary: "Built the greeting module.",
          written_at: "2026-07-02T18:30:00+00:00",
        },
      },
    } as Partial<Task>);
    const { getByTestId } = render(<TabNotes task={task} />);
    expect(getByTestId("written-at-dev_notes").textContent).toContain("Jul");
  });

  it("renders no stamp when the section has none (pre-stamp rows)", () => {
    const task = buildTask({
      dev_notes: "Legacy note without a structured stamp.",
      notes_structured: { developer: { summary: "Legacy note." } },
    } as Partial<Task>);
    const { queryByTestId } = render(<TabNotes task={task} />);
    expect(queryByTestId("written-at-dev_notes")).toBeNull();
  });
});
