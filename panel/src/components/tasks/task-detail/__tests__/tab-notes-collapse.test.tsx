import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import React from "react";
import { TaskStatus, Team, TaskType, type Task } from "@/types";
import { READABILITY_CHAR_THRESHOLD } from "@/lib/content-readability";

// EditableNoteCard drives CollapsibleSection with a controlled `open`, so the
// content-length-based default has to be computed at the card level (mirrors
// the same content-readability spec CollapsibleSection itself uses).

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
    created_at: "2026-06-01T12:00:00+00:00",
    ...overrides,
  } as unknown as Task;
}

describe("notes tab content-driven collapse", () => {
  it("renders a long dev_notes field collapsed by default", () => {
    const task = buildTask({
      dev_notes: "a".repeat(READABILITY_CHAR_THRESHOLD + 1),
    });
    render(<TabNotes task={task} />);
    expect(
      screen.getByRole("button", { name: /Developer Notes/ }),
    ).toHaveAttribute("aria-expanded", "false");
  });

  it("renders a short dev_notes field expanded by default", () => {
    const task = buildTask({ dev_notes: "Built the greeting module." });
    render(<TabNotes task={task} />);
    expect(
      screen.getByRole("button", { name: /Developer Notes/ }),
    ).toHaveAttribute("aria-expanded", "true");
  });
});
