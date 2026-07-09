import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";
import React from "react";
import { TaskStatus, Team, TaskType, type Task } from "@/types";

// God-mode status override: forcing a task into a hatch/terminal state via the
// header Select must send `force: true`, else the backend refuses the PATCH
// with 400 (the lifecycle-bypass acknowledgement added by the gap sweep).

const { mutateAsync } = vi.hoisted(() => ({
  mutateAsync: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), back: vi.fn() }),
}));

vi.mock("@/hooks/use-tasks", () => ({
  useUpdateTask: () => ({ mutateAsync, isPending: false }),
  useDeleteTask: () => ({ mutateAsync: vi.fn(), isPending: false }),
  // Empty valid-transitions => every other status is a god-mode override.
  useTaskValidTransitions: () => ({ data: [], isLoading: false }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

// Make the Select testable without Radix's portal/pointer machinery: each
// SelectItem renders a button carrying its value; clicking it invokes the
// nearest Select's onValueChange (scoped via context so the status Select and
// the team Select don't cross-fire).
vi.mock("@/components/ui/select", () => {
  const Ctx = React.createContext<(v: string) => void>(() => {});
  return {
    Select: ({
      onValueChange,
      children,
    }: {
      onValueChange?: (v: string) => void;
      children: React.ReactNode;
    }) => (
      <Ctx.Provider value={onValueChange ?? (() => {})}>
        {children}
      </Ctx.Provider>
    ),
    SelectTrigger: ({ children }: { children: React.ReactNode }) => (
      <div>{children}</div>
    ),
    SelectValue: () => null,
    SelectContent: ({ children }: { children: React.ReactNode }) => (
      <div>{children}</div>
    ),
    SelectItem: ({
      value,
      children,
    }: {
      value: string;
      children: React.ReactNode;
    }) => {
      const onValueChange = React.useContext(Ctx);
      return (
        <button data-value={value} onClick={() => onValueChange(value)}>
          {children}
        </button>
      );
    },
  };
});

import { TaskHeader } from "../task-header";

function buildTask(): Task {
  return {
    id: "t1",
    title: "Wedged task",
    description: "d",
    status: TaskStatus.IN_PROGRESS,
    team: Team.BACKEND,
    task_type: TaskType.CODE,
    acceptance_criteria: [],
  } as unknown as Task;
}

describe("TaskHeader god-mode status override", () => {
  it("sends force: true when forcing a hatch/terminal status", async () => {
    const { container } = render(<TaskHeader task={buildTask()} />);
    const btn = container.querySelector<HTMLButtonElement>(
      `[data-value="${TaskStatus.COMPLETED}"]`,
    );
    expect(btn).not.toBeNull();
    btn?.click();
    await vi.waitFor(() =>
      expect(mutateAsync).toHaveBeenCalledWith({
        taskId: "t1",
        updates: { status: TaskStatus.COMPLETED, force: true },
      }),
    );
  });
});
