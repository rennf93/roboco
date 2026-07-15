import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import React from "react";
import { TaskStatus, Team, TaskType, type Task } from "@/types";
import type { TaskFindingsResponse } from "@/lib/api/tasks";

// Mirrors tab-progress-collapse.test.tsx's shape: mock the data hook
// directly rather than wiring a real QueryClient — TabFindings only reads
// the hook's return value.
const { useTaskFindings } = vi.hoisted(() => ({ useTaskFindings: vi.fn() }));

vi.mock("@/hooks/use-tasks", () => ({ useTaskFindings }));

// CodeSnippet runs a real useQuery (needs a QueryClient); stub it so the
// findings test stays focused on grouping/rendering, not git fetching.
vi.mock("@/components/git/code-snippet", () => ({
  CodeSnippet: () => <div data-testid="code-snippet" />,
}));

import { TabFindings } from "../tab-findings";

function buildTask(overrides: Partial<Task> = {}): Task {
  return {
    id: "t1",
    title: "Task",
    description: "d",
    status: TaskStatus.NEEDS_REVISION,
    team: Team.BACKEND,
    task_type: TaskType.CODE,
    acceptance_criteria: [],
    ...overrides,
  } as unknown as Task;
}

describe("TabFindings", () => {
  it("renders the empty state when the task was never bounced", () => {
    useTaskFindings.mockReturnValue({
      data: { findings: [], summary: [], total: 0, truncated: false },
    });
    render(<TabFindings task={buildTask()} />);
    expect(
      screen.getByText("No revision findings recorded yet."),
    ).toBeInTheDocument();
  });

  it("groups findings by round and renders severity/status", () => {
    const response: TaskFindingsResponse = {
      findings: [
        {
          id: "aaaaaaaa-0000-0000-0000-000000000000",
          task_id: "t1",
          origin: "qa",
          round: 2,
          author_slug: "be-qa",
          file: "roboco/services/task.py",
          line: 42,
          severity: "blocker",
          criterion: null,
          expected: "the endpoint returns 404",
          actual: "the endpoint returns 500",
          fix: "add a not-found guard",
          evidence: null,
          status: "open",
          addressed_by_commit: null,
          resolution_note: null,
          created_at: "2026-07-11T00:00:00Z",
          updated_at: null,
        },
        {
          id: "bbbbbbbb-0000-0000-0000-000000000000",
          task_id: "t1",
          origin: "qa",
          round: 1,
          author_slug: "be-qa",
          file: "roboco/services/task.py",
          line: 10,
          severity: "minor",
          criterion: null,
          expected: "x",
          actual: "y",
          fix: null,
          evidence: null,
          status: "verified",
          addressed_by_commit: "abc1234def",
          resolution_note: null,
          created_at: "2026-07-10T00:00:00Z",
          updated_at: null,
        },
      ],
      summary: [
        { origin: "qa", open: 1, addressed: 0, verified: 1, waived: 0 },
      ],
      total: 2,
      truncated: false,
    };
    useTaskFindings.mockReturnValue({ data: response, isLoading: false });
    render(<TabFindings task={buildTask()} />);

    expect(screen.getByText("Round 2")).toBeInTheDocument();
    expect(screen.getByText("Round 1")).toBeInTheDocument();
    expect(screen.getByText("blocker")).toBeInTheDocument();
    expect(screen.getByText("minor")).toBeInTheDocument();
    expect(screen.getByText("abc1234")).toBeInTheDocument();
    expect(screen.queryByText(/more not shown/)).toBeNull();
  });

  it("annotates a truncated ledger with the not-shown remainder", () => {
    const response: TaskFindingsResponse = {
      findings: [
        {
          id: "cccccccc-0000-0000-0000-000000000000",
          task_id: "t1",
          origin: "qa",
          round: 1,
          author_slug: "be-qa",
          file: null,
          line: null,
          severity: "minor",
          criterion: null,
          expected: "x",
          actual: "y",
          fix: null,
          evidence: null,
          status: "open",
          addressed_by_commit: null,
          resolution_note: null,
          created_at: "2026-07-11T00:00:00Z",
          updated_at: null,
        },
      ],
      summary: [
        { origin: "qa", open: 501, addressed: 0, verified: 0, waived: 0 },
      ],
      total: 501,
      truncated: true,
    };
    useTaskFindings.mockReturnValue({ data: response, isLoading: false });
    render(<TabFindings task={buildTask()} />);
    expect(
      screen.getByText("… 500 more not shown (501 total)"),
    ).toBeInTheDocument();
  });
});
