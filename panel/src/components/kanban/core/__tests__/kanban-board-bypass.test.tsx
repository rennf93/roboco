import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { TaskStatus, Team, TaskType, type Task } from "@/types";

// Capture the board's onDragEnd so the test can synthesize a drop without
// driving the real dnd-kit pointer sensor (painful in jsdom).
const dragRef = vi.hoisted(() => ({
  onDragEnd: null as ((e: unknown) => void) | null,
}));

vi.mock("@dnd-kit/core", () => ({
  DndContext: ({
    onDragEnd,
    children,
  }: {
    onDragEnd: (e: unknown) => void;
    children: React.ReactNode;
  }) => {
    dragRef.onDragEnd = onDragEnd;
    return <>{children}</>;
  },
  DragOverlay: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  PointerSensor: () => null,
  useSensor: () => null,
  useSensors: () => [],
}));

const { mutateAsync, refetch, tasksRef } = vi.hoisted(() => ({
  mutateAsync: vi.fn().mockResolvedValue(undefined),
  refetch: vi.fn().mockResolvedValue(undefined),
  tasksRef: { current: [] as Task[] },
}));

vi.mock("@/hooks/use-tasks", () => ({
  useTasks: () => ({ data: tasksRef.current, isLoading: false, refetch }),
  useTaskLifecycle: () => ({
    claim: { mutateAsync: vi.fn() },
    start: { mutateAsync: vi.fn() },
    block: { mutateAsync: vi.fn() },
    unblock: { mutateAsync: vi.fn() },
    pause: { mutateAsync: vi.fn() },
    resume: { mutateAsync: vi.fn() },
    verify: { mutateAsync: vi.fn() },
    submitQa: { mutateAsync: vi.fn() },
    passQa: { mutateAsync: vi.fn() },
    failQa: { mutateAsync: vi.fn() },
    complete: { mutateAsync: vi.fn() },
  }),
  useUpdateTask: () => ({ mutateAsync, isPending: false }),
}));

// Avoid rendering the real columns/cards — the bypass gate lives in the board's
// drag handler, not in the column children.
vi.mock("../kanban-column", () => ({ KanbanColumn: () => null }));
vi.mock("../kanban-card", () => ({ KanbanCard: () => null }));
vi.mock("@/components/tasks/task-detail/task-action-dialogs", () => ({
  RequiredNotesDialog: () => null,
}));

import { KanbanBoard } from "../kanban-board";

function buildTask(overrides: Partial<Task> = {}): Task {
  return {
    id: "t1",
    title: "A task",
    description: "desc",
    acceptance_criteria: ["a"],
    status: TaskStatus.IN_PROGRESS,
    priority: 2,
    sequence: 0,
    team: Team.BACKEND,
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
    estimated_complexity: "M" as never,
    nature: "feature" as never,
    task_type: TaskType.CODE,
    project_id: "p1",
    docs_complete: false,
    pr_created: false,
    pm_approvals: {},
    plan: null,
    checkpoints: [],
    progress_updates: [],
    commits: [],
    self_verified: false,
    qa_verified: null,
    sessions: [],
    branch_name: null,
    pr_number: null,
    pr_url: null,
    ...overrides,
  } as unknown as Task;
}

const COLUMNS = [
  { id: "pending", status: TaskStatus.PENDING, title: "Pending", color: "" },
  {
    id: "in_progress",
    status: TaskStatus.IN_PROGRESS,
    title: "In Progress",
    color: "",
  },
  { id: "completed", status: TaskStatus.COMPLETED, title: "Done", color: "" },
];

function drop(activeId: string, overId: TaskStatus) {
  dragRef.onDragEnd?.({ active: { id: activeId }, over: { id: overId } });
}

describe("KanbanBoard — admin-override bypass confirmation (F020)", () => {
  beforeEach(() => {
    mutateAsync.mockClear();
    refetch.mockClear();
    tasksRef.current = [];
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("confirms before a drag that skips lifecycle preconditions, then fires the override on confirm", async () => {
    tasksRef.current = [
      buildTask({ id: "t1", status: TaskStatus.IN_PROGRESS, pr_number: null }),
    ];
    render(<KanbanBoard title="Board" columns={COLUMNS} />);

    // Drag a PR-less task straight to Done — completing with no open PR skips
    // the in-band gate. The board must NOT fire the override silently; it must
    // surface what's skipped and wait for an explicit confirm.
    drop("t1", TaskStatus.COMPLETED);

    // The board holds the move and surfaces what's skipped — it must NOT fire
    // the admin status-override silently.
    await screen.findByText(/no open pr/i);
    expect(
      screen.getByText(/documentation not marked complete/i),
    ).toBeInTheDocument();
    expect(mutateAsync).not.toHaveBeenCalled();

    // Confirm the override — now the admin status-override fires.
    fireEvent.click(screen.getByRole("button", { name: /override & move/i }));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    expect(mutateAsync).toHaveBeenCalledWith({
      taskId: "t1",
      updates: { status: TaskStatus.COMPLETED },
    });
  });

  it("does not fire the override when the confirmation is cancelled", async () => {
    tasksRef.current = [
      buildTask({ id: "t1", status: TaskStatus.IN_PROGRESS, pr_number: null }),
    ];
    render(<KanbanBoard title="Board" columns={COLUMNS} />);

    drop("t1", TaskStatus.COMPLETED);
    await screen.findByText(/no open pr/i);

    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));

    await waitFor(() =>
      expect(screen.queryByText(/no open pr/i)).not.toBeInTheDocument(),
    );
    expect(mutateAsync).not.toHaveBeenCalled();
  });

  it("fires the move directly when the drag skips no material precondition", async () => {
    // pending -> claimed gates on nothing the panel can check — no dialog.
    tasksRef.current = [
      buildTask({ id: "t1", status: TaskStatus.PENDING, pr_number: null }),
    ];
    render(<KanbanBoard title="Board" columns={COLUMNS} />);

    drop("t1", TaskStatus.CLAIMED);

    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    expect(mutateAsync).toHaveBeenCalledWith({
      taskId: "t1",
      updates: { status: TaskStatus.CLAIMED },
    });
    // No bypass confirmation should ever have been surfaced.
    expect(screen.queryByText(/override & move/i)).not.toBeInTheDocument();
  });
});
