import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";
import React from "react";
import { TaskStatus, Team, TaskType, type Task } from "@/types";

// awaiting_ceo_approval must offer the PROVEN approval path: the
// CeoApproveDialog -> POST /tasks/{id}/ceo-approve (notes >= 20 chars).
// The old wiring offered only approve-and-merge, which 400s NO_PR on a
// branchless MegaTask umbrella — the CEO's approve button just failed.

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

vi.mock("@/components/ui/select", () => ({
  Select: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  SelectTrigger: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  SelectValue: () => null,
  SelectContent: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  SelectItem: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
}));

// Render the dropdown inline so the action items are clickable without
// Radix's portal/pointer machinery.
vi.mock("@/components/ui/dropdown-menu", () => ({
  DropdownMenu: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  DropdownMenuTrigger: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  DropdownMenuContent: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  DropdownMenuItem: ({
    children,
    onClick,
  }: {
    children: React.ReactNode;
    onClick?: () => void;
  }) => (
    <button type="button" onClick={onClick}>
      {children}
    </button>
  ),
  DropdownMenuSeparator: () => null,
}));

import { TaskHeader } from "../task-header";

function buildTask(overrides: Partial<Task> = {}): Task {
  return {
    id: "t1",
    title: "Umbrella awaiting CEO",
    description: "d",
    status: TaskStatus.AWAITING_CEO_APPROVAL,
    team: Team.BACKEND,
    task_type: TaskType.CODE,
    acceptance_criteria: [],
    ...overrides,
  } as unknown as Task;
}

describe("TaskHeader awaiting_ceo_approval actions", () => {
  it("offers Approve & Complete wired to the ceo-approve dialog", () => {
    const onAction = vi.fn();
    const { getByText } = render(
      <TaskHeader task={buildTask()} onAction={onAction} />,
    );
    getByText("Approve & Complete").click();
    expect(onAction).toHaveBeenCalledWith("ceo-approve");
  });

  it("hides Approve & Merge when the task has no PR (umbrella)", () => {
    const { queryByText } = render(
      <TaskHeader task={buildTask()} onAction={vi.fn()} />,
    );
    expect(queryByText("Approve & Merge")).toBeNull();
  });

  it("still offers Approve & Merge for a PR-bearing task", () => {
    const onAction = vi.fn();
    const { getByText } = render(
      <TaskHeader task={buildTask({ pr_number: 42 })} onAction={onAction} />,
    );
    getByText("Approve & Merge").click();
    expect(onAction).toHaveBeenCalledWith("approve-and-merge");
  });
});
