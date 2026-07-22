import { describe, it, expect, vi, beforeEach } from "vitest";
import { render as rtlRender, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { toast } from "sonner";
import { TgTaskSheet } from "../tg-task-sheet";
import type { Task } from "@/types";
import type { TaskFindingsResponse } from "@/lib/api/tasks";

const { findings, ceoApprove, ceoReject, unblock } = vi.hoisted(() => ({
  findings: vi.fn<() => { data: TaskFindingsResponse | undefined }>(() => ({
    data: undefined,
  })),
  ceoApprove: vi.fn(),
  ceoReject: vi.fn(),
  unblock: vi.fn(),
}));
vi.mock("@/hooks/use-tasks", () => ({
  useTaskFindings: findings,
  taskKeys: { all: ["tasks"] },
}));
vi.mock("@/lib/api/tasks", () => ({
  tasksApi: { ceoApprove, ceoReject, unblock },
}));
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

// The sheet's CEO action block mutates through react-query.
function render(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return rtlRender(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
}

function task(overrides: Partial<Task> = {}): Task {
  return {
    id: "t1",
    title: "Harden the retry queue",
    description: "Webhook retries with backoff.",
    acceptance_criteria: ["Retries back off", "DLQ after 5 attempts"],
    status: "awaiting_ceo_approval",
    priority: 1,
    sequence: 0,
    team: "backend",
    created_by: "main-pm",
    assigned_to: "be-dev-1",
    parent_task_id: null,
    dependency_ids: [],
    blocker_ids: [],
    created_at: "2026-07-18T10:00:00Z",
    updated_at: "2026-07-19T09:00:00Z",
    claimed_at: null,
    started_at: null,
    completed_at: null,
    target_date: null,
    estimated_complexity: "medium",
    nature: "technical",
    task_type: "code",
    project_id: "p1",
    docs_complete: false,
    pr_created: true,
    pm_approvals: {},
    plan: null,
    checkpoints: [],
    progress_updates: [],
    commits: [],
    dev_notes: null,
    qa_notes: null,
    auditor_notes: null,
    quick_context: null,
    self_verified: true,
    qa_verified: true,
    revision_count: 2,
    branch_name: "feature/backend/T1",
    pr_number: 612,
    pr_url: "https://example.com/pull/612",
    ...overrides,
  } as Task;
}

beforeEach(() => {
  ceoApprove.mockReset();
  ceoReject.mockReset();
  unblock.mockReset();
  vi.mocked(toast.success).mockClear();
  vi.mocked(toast.error).mockClear();
});

describe("TgTaskSheet", () => {
  it("renders nothing without a task", () => {
    render(<TgTaskSheet task={null} onClose={vi.fn()} />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("shows title, ACs, bounce chip, and the PR link", () => {
    render(<TgTaskSheet task={task()} onClose={vi.fn()} />);
    expect(screen.getByText("Harden the retry queue")).toBeInTheDocument();
    expect(screen.getByText("Retries back off")).toBeInTheDocument();
    expect(screen.getByText("DLQ after 5 attempts")).toBeInTheDocument();
    expect(screen.getByText(/bounced ×2/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /open pr #612/i })).toHaveAttribute(
      "href",
      "https://example.com/pull/612",
    );
  });

  it("lists only the open findings", () => {
    findings.mockReturnValue({
      data: {
        findings: [
          {
            id: "f1",
            task_id: "t1",
            origin: "qa",
            round: 1,
            author_slug: "be-qa",
            file: "roboco/services/queue.py",
            line: 42,
            severity: "major",
            criterion: null,
            expected: "Backoff is exponential",
            actual: "Fixed 1s delay",
            fix: "Use exponential backoff with jitter",
            evidence: null,
            status: "open",
            addressed_by_commit: null,
            resolution_note: null,
            created_at: "2026-07-19T08:00:00Z",
            updated_at: null,
          },
          {
            id: "f2",
            task_id: "t1",
            origin: "qa",
            round: 1,
            author_slug: "be-qa",
            file: "roboco/services/dlq.py",
            line: 7,
            severity: "minor",
            criterion: null,
            expected: "x",
            actual: "y",
            fix: null,
            evidence: null,
            status: "verified",
            addressed_by_commit: null,
            resolution_note: null,
            created_at: "2026-07-19T08:00:00Z",
            updated_at: null,
          },
        ],
        summary: [],
        total: 2,
        truncated: false,
      },
    });
    render(<TgTaskSheet task={task()} onClose={vi.fn()} />);
    expect(screen.getByText(/open findings · 1/i)).toBeInTheDocument();
    expect(screen.getByText("roboco/services/queue.py:42")).toBeInTheDocument();
    expect(screen.queryByText(/dlq\.py/)).not.toBeInTheDocument();
  });

  it("offers Approve / Request changes on an awaiting-CEO task", () => {
    render(<TgTaskSheet task={task()} onClose={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Request changes" }),
    ).toBeInTheDocument();
  });

  it("offers Unblock on a blocked task and no CEO verbs elsewhere", () => {
    render(
      <TgTaskSheet
        task={task({ status: "blocked" as Task["status"] })}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "Unblock" })).toBeInTheDocument();

    render(
      <TgTaskSheet
        task={task({ id: "t2", status: "in_progress" as Task["status"] })}
        onClose={vi.fn()}
      />,
    );
    expect(
      screen.queryByRole("button", { name: "Approve" }),
    ).not.toBeInTheDocument();
  });
});

describe("TgTaskSheet — CEO action interactions", () => {
  it("clicking Approve calls ceoApprove with the task id and toasts success", async () => {
    ceoApprove.mockResolvedValue({});
    render(<TgTaskSheet task={task()} onClose={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: "Approve" }));

    expect(ceoApprove).toHaveBeenCalledWith("t1");
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith("Approved"),
    );
  });

  it("shows the error toast when approve's promise rejects", async () => {
    ceoApprove.mockRejectedValue(new Error("Approve failed"));
    render(<TgTaskSheet task={task()} onClose={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: "Approve" }));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("Approve failed"),
    );
    expect(ceoReject).not.toHaveBeenCalled();
  });

  it("keeps Send back for revision disabled under the 10-char reason floor", async () => {
    render(<TgTaskSheet task={task()} onClose={vi.fn()} />);

    await userEvent.click(
      screen.getByRole("button", { name: "Request changes" }),
    );
    const textarea = screen.getByPlaceholderText(/at least 10 characters/i);
    const sendBack = screen.getByRole("button", {
      name: "Send back for revision",
    });
    expect(sendBack).toBeDisabled();

    await userEvent.type(textarea, "too short");
    expect(sendBack).toBeDisabled();
    expect(ceoReject).not.toHaveBeenCalled();
  });

  it("enables Send back at 10+ chars and calls ceoReject with the id and reason", async () => {
    ceoReject.mockResolvedValue({});
    render(<TgTaskSheet task={task()} onClose={vi.fn()} />);

    await userEvent.click(
      screen.getByRole("button", { name: "Request changes" }),
    );
    await userEvent.type(
      screen.getByPlaceholderText(/at least 10 characters/i),
      "Please redo the retry backoff",
    );
    const sendBack = screen.getByRole("button", {
      name: "Send back for revision",
    });
    expect(sendBack).toBeEnabled();

    await userEvent.click(sendBack);

    expect(ceoReject).toHaveBeenCalledWith("t1", "Please redo the retry backoff");
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith("Sent back for revision"),
    );
  });
});
