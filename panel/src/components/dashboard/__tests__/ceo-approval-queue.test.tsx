import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { TaskStatus, Team, type Task } from "@/types";

// Control useQuery per call (the component runs two queries); the mutation +
// queryClient hooks just need to exist.
const { mockUseQuery } = vi.hoisted(() => ({
  mockUseQuery: vi.fn(),
}));

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-query")>();
  return {
    ...actual,
    useQuery: mockUseQuery,
    useMutation: vi.fn(() => ({ mutate: vi.fn(), mutateAsync: vi.fn() })),
    useQueryClient: vi.fn(() => ({})),
  };
});

// tasksApi methods never run (useQuery/useMutation are mocked), but the
// component imports tasksApi from this barrel — provide a stub object.
vi.mock("@/lib/api", () => ({
  tasksApi: {
    getAwaitingCeoApproval: vi.fn(),
    list: vi.fn(),
    approve: vi.fn(),
    reject: vi.fn(),
    approveAndStart: vi.fn(),
  },
}));

import { CeoApprovalQueue } from "../ceo-approval-queue";

function buildTask(): Task {
  return {
    id: "task-1",
    title: "Add timestamp footer",
    description: "objective objective objective",
    acceptance_criteria: ["a"],
    status: TaskStatus.AWAITING_CEO_APPROVAL,
    priority: 1,
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
    task_type: "code" as never,
    project_id: "p1",
    docs_complete: false,
    pr_created: false,
    pm_approvals: {},
    plan: null,
  } as unknown as Task;
}

describe("CeoApprovalQueue — Approve dialog notes label (F081)", () => {
  beforeEach(() => {
    // The awaiting-ceo-approval query returns one task (renders an Approve
    // row); the awaiting-approve-start query returns none (no Approve&Start).
    mockUseQuery.mockImplementation((opts: { queryKey: string[] }) => {
      const kind = opts.queryKey[1];
      if (kind === "awaiting-ceo-approval") {
        return { data: [buildTask()], isLoading: false };
      }
      return { data: [], isLoading: false };
    });
  });

  it("the Approve dialog tells the CEO notes are required (>= 20 chars), not optional", () => {
    render(<CeoApprovalQueue />);

    // Open the Approve dialog for the one pending task.
    const approveBtn = screen.getByRole("button", { name: "Approve" });
    fireEvent.click(approveBtn);

    // The notes label must signal required + the >= 20 char minimum the
    // client (and server) enforce. Before the fix it read "Notes (optional)"
    // while approve actually requires >= 20 substantive chars — so the CEO
    // could type nothing, hit submit, and only learn the requirement from a
    // toast error.
    const label = document.querySelector('label[for="notes"]');
    expect(label).not.toBeNull();
    const text = label!.textContent ?? "";
    expect(text).not.toMatch(/optional/i);
    expect(text).toMatch(/required/i);
    expect(text).toMatch(/20/);
  });
});
