import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import React from "react";
import { TaskStatus, Team, TaskType, type Task } from "@/types";

// AC4 regression: a task carrying a long progress history AND a long
// acceptance-criteria list used to render everything fully expanded at
// once, forcing continuous scrolling through both sections. Both now
// default to collapsed content per the content-readability spec, so the
// tab stays navigable.

vi.mock("@/hooks/use-tasks", () => ({
  useUpdateTask: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { TabProgress } from "../tab-progress";
import { AcceptanceCriteria } from "../acceptance-criteria";

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

function makeLongCriteria(count: number) {
  return Array.from(
    { length: count },
    (_, i) => `[ ] Criterion ${i} requires a fairly detailed description to be meaningful`,
  );
}

describe("Task detail readability: long progress history + long criteria list", () => {
  it("keeps a 30+ entry progress history navigable — only the 2 most recent default open", () => {
    const task = buildTask({ progress_updates: makeUpdates(32) });
    const { container } = render(<TabProgress task={task} />);

    const triggers = Array.from(
      container.querySelectorAll("li button[data-state]"),
    );
    expect(triggers).toHaveLength(32);
    const openCount = triggers.filter(
      (t) => t.getAttribute("data-state") === "open",
    ).length;
    expect(openCount).toBe(2);
  });

  it("collapses a long acceptance-criteria list by default so the page doesn't open fully expanded", () => {
    const task = buildTask({ acceptance_criteria: makeLongCriteria(20) });
    render(<AcceptanceCriteria task={task} />);

    expect(
      screen.getByRole("button", { name: /acceptance criteria/i }),
    ).toHaveAttribute("aria-expanded", "false");
  });

  it("keeps a short acceptance-criteria list expanded (no regression for the common case)", () => {
    const task = buildTask({ acceptance_criteria: makeLongCriteria(2) });
    render(<AcceptanceCriteria task={task} />);

    expect(
      screen.getByRole("button", { name: /acceptance criteria/i }),
    ).toHaveAttribute("aria-expanded", "true");
  });
});
