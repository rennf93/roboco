import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import React from "react";
import { TaskStatus, Team, TaskType, type Task } from "@/types";

// Regression: wrapping each TabsTrigger in a Tooltip (e211a3c1) collides with
// Radix Tabs' own data-state — TooltipTrigger's asChild Slot merge injects
// the tooltip's own data-state ("closed"/"open") onto the underlying
// TabsTrigger, and Radix Tabs' internal render spreads incoming props AFTER
// its own literal data-state, so the tooltip's value always wins. Every
// trigger ends up data-state="closed" regardless of selection, so the
// data-[state=active] highlight in ui/tabs.tsx never fires for any tab.

const mockReplace = vi.fn();
let searchParams = new URLSearchParams();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockReplace }),
  usePathname: () => "/tasks/t1",
  useSearchParams: () => searchParams,
}));

vi.mock("@/hooks/use-tasks", () => ({
  useTaskFindings: () => ({ data: undefined }),
}));

// The tab panes aren't under test here — stub them so the strip's highlight
// behavior can be verified without wiring react-query/task fixtures for
// every pane (mirrors tab-findings.test.tsx stubbing CodeSnippet).
vi.mock("../tab-overview", () => ({
  TabOverview: () => <div data-testid="pane-overview" />,
}));
vi.mock("../tab-plan", () => ({
  TabPlan: () => <div data-testid="pane-plan" />,
}));
vi.mock("../tab-progress", () => ({
  TabProgress: () => <div data-testid="pane-progress" />,
}));
vi.mock("../tab-commits", () => ({
  TabCommits: () => <div data-testid="pane-commits" />,
}));
vi.mock("../tab-notes", () => ({
  TabNotes: () => <div data-testid="pane-notes" />,
}));
vi.mock("../tab-dependencies", () => ({
  TabDependencies: () => <div data-testid="pane-deps" />,
}));
vi.mock("../tab-findings", () => ({
  TabFindings: () => <div data-testid="pane-findings" />,
}));
vi.mock("../tab-collision", () => ({
  TabCollision: () => <div data-testid="pane-collision" />,
}));

import { TaskTabs } from "../task-tabs";

function buildTask(overrides: Partial<Task> = {}): Task {
  return {
    id: "t1",
    title: "Task",
    description: "d",
    status: TaskStatus.IN_PROGRESS,
    team: Team.BACKEND,
    task_type: TaskType.CODE,
    acceptance_criteria: [],
    progress_updates: [],
    checkpoints: [],
    commits: [],
    dependency_ids: [],
    blocker_ids: [],
    plan: null,
    dev_notes: null,
    qa_notes: null,
    auditor_notes: null,
    quick_context: null,
    ...overrides,
  } as unknown as Task;
}

describe("TaskTabs active-tab highlight", () => {
  beforeEach(() => {
    searchParams = new URLSearchParams();
    mockReplace.mockClear();
  });

  it("marks only the default (Overview) tab's trigger active", () => {
    render(<TaskTabs task={buildTask()} />);

    const overviewTrigger = screen.getByRole("tab", { name: /Overview/i });
    expect(overviewTrigger).toHaveAttribute("data-state", "active");

    const others = screen
      .getAllByRole("tab")
      .filter((t) => t !== overviewTrigger);
    expect(others).toHaveLength(7);
    for (const trigger of others) {
      expect(trigger).toHaveAttribute("data-state", "inactive");
    }
  });

  it("moves the active data-state to whichever tab the URL selects", () => {
    searchParams = new URLSearchParams("tab=commits");
    render(<TaskTabs task={buildTask()} />);

    expect(screen.getByRole("tab", { name: /Commits/i })).toHaveAttribute(
      "data-state",
      "active",
    );
    expect(screen.getByRole("tab", { name: /Overview/i })).toHaveAttribute(
      "data-state",
      "inactive",
    );
  });
});
