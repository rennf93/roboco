import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import React from "react";
import { TaskStatus, Team, TaskType, type Task } from "@/types";

// The server-derived task.constraints field (TaskService._attach_baseline_constraints,
// "moved out of description") must render with a visually distinct treatment, and
// the description section must stay independently collapsible/editable like before.

vi.mock("@/hooks/use-tasks", () => ({
  useUpdateTask: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { TaskDescription } from "../task-description";

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

describe("TaskDescription", () => {
  it("renders task.constraints in its own distinctly-styled, collapsible section", () => {
    const task = buildTask({
      description: "Implement the thing.",
      constraints: "- Route handlers must stay thin\n- No models in routes",
    });
    render(<TaskDescription task={task} />);

    expect(screen.getByText("Implement the thing.")).toBeInTheDocument();

    // Two independent toggles: one for Description, one for Constraints.
    // Constraints is project boilerplate, so it ALWAYS starts collapsed.
    const constraintsToggle = screen.getByRole("button", {
      name: "Constraints",
    });
    expect(constraintsToggle).toHaveAttribute("aria-expanded", "false");
    expect(
      screen.queryByText("Route handlers must stay thin"),
    ).not.toBeInTheDocument();
    fireEvent.click(constraintsToggle);
    expect(constraintsToggle).toHaveAttribute("aria-expanded", "true");
    expect(
      screen.getByText("Route handlers must stay thin"),
    ).toBeInTheDocument();
    // Expanding Constraints doesn't affect the Description section.
    expect(screen.getByText("Implement the thing.")).toBeVisible();
  });

  it("renders no constraints section when the task carries none", () => {
    const task = buildTask({
      description: "Just a plain description.",
      constraints: null,
    });
    render(<TaskDescription task={task} />);
    expect(screen.queryByText("Constraints")).not.toBeInTheDocument();
  });

  it("collapses and re-expands the description body via the header toggle", () => {
    const task = buildTask({ description: "Some collapsible content." });
    render(<TaskDescription task={task} />);

    const toggle = screen.getByRole("button", { name: "Description" });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Some collapsible content.")).toBeVisible();

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(
      screen.queryByText("Some collapsible content."),
    ).not.toBeInTheDocument();

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Some collapsible content.")).toBeVisible();
  });

  it("keeps the edit/preview toggle working alongside the new collapse behavior", () => {
    const task = buildTask({ description: "Editable text." });
    render(<TaskDescription task={task} />);

    fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
    expect(
      screen.getByPlaceholderText("Add a description..."),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: /preview/i }));
    expect(screen.getByText("Editable text.")).toBeInTheDocument();
  });
});
