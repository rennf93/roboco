import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type ReactNode } from "react";

const { get, getFindings, conventionsFindings } = vi.hoisted(() => ({
  get: vi.fn(),
  getFindings: vi.fn(),
  conventionsFindings: vi.fn(),
}));

vi.mock("@/lib/api/tasks", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/api/tasks")>("@/lib/api/tasks");
  return {
    ...actual,
    tasksApi: { ...actual.tasksApi, get, getFindings },
  };
});

vi.mock("@/lib/api/conventions", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/conventions")>(
    "@/lib/api/conventions",
  );
  return {
    ...actual,
    conventionsApi: { ...actual.conventionsApi, findings: conventionsFindings },
  };
});

import {
  useAcVerificationStamps,
  useFindingsByRound,
  usePrCiVerdict,
  useReviewerChain,
  useTaskConventionFindings,
} from "@/hooks/use-verification";
import type { Task } from "@/types";
import type { TaskFinding, TaskFindingsResponse } from "@/lib/api/tasks";
import type { ConventionFinding } from "@/lib/api/conventions";

function finding(overrides: Partial<TaskFinding>): TaskFinding {
  return {
    id: "f-1",
    task_id: "t1",
    origin: "qa",
    round: 1,
    author_slug: "fe-qa",
    file: null,
    line: null,
    severity: "major",
    criterion: null,
    expected: "expected",
    actual: "actual",
    fix: null,
    evidence: null,
    status: "open",
    addressed_by_commit: null,
    resolution_note: null,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: null,
    ...overrides,
  };
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("useAcVerificationStamps", () => {
  beforeEach(() => {
    get.mockReset();
  });

  it("derives per-AC verification state from the task's own qa_notes", async () => {
    get.mockResolvedValue({
      id: "t1",
      acceptance_criteria: ["Criterion A", "Criterion B"],
      qa_notes: "[AC] Criterion A — verified: file.ts:10",
    } as Task);

    const { result } = renderHook(() => useAcVerificationStamps("t1"), {
      wrapper,
    });

    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data).toEqual([
      { criterion: "Criterion A", verified: true, evidence: "file.ts:10" },
      { criterion: "Criterion B", verified: false, evidence: null },
    ]);
  });
});

describe("useReviewerChain", () => {
  beforeEach(() => {
    getFindings.mockReset();
  });

  it("derives the round-by-round reviewer chain from the findings ledger", async () => {
    const response: TaskFindingsResponse = {
      findings: [
        {
          id: "f1",
          task_id: "t1",
          origin: "qa",
          round: 1,
          author_slug: "fe-qa",
          file: null,
          line: null,
          severity: "major",
          criterion: null,
          expected: "x",
          actual: "y",
          fix: null,
          evidence: null,
          status: "open",
          addressed_by_commit: null,
          resolution_note: null,
          created_at: "2026-09-01T00:00:00Z",
          updated_at: null,
        },
      ],
      summary: [],
      total: 1,
      truncated: false,
    };
    getFindings.mockResolvedValue(response);

    const { result } = renderHook(() => useReviewerChain("t1"), { wrapper });

    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data).toEqual([
      { round: 1, origin: "qa", author_slug: "fe-qa", model: null },
    ]);
  });
});

describe("useFindingsByRound", () => {
  beforeEach(() => {
    getFindings.mockReset();
  });

  it("groups the same findings ledger query into rounds, newest first", async () => {
    const response: TaskFindingsResponse = {
      findings: [
        finding({ id: "f-2", round: 2, origin: "pr_gate", severity: "nit" }),
        finding({ id: "f-1", round: 1, origin: "qa", severity: "blocker" }),
      ],
      summary: [],
      total: 2,
      truncated: false,
    };
    getFindings.mockResolvedValue(response);

    const { result } = renderHook(() => useFindingsByRound("t1"), {
      wrapper,
    });

    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data).toEqual([
      { round: 2, origin: "pr_gate", findings: [response.findings[0]] },
      { round: 1, origin: "qa", findings: [response.findings[1]] },
    ]);
    expect(getFindings).toHaveBeenCalledTimes(1);
  });
});

describe("useTaskConventionFindings", () => {
  beforeEach(() => {
    get.mockReset();
    conventionsFindings.mockReset();
  });

  it("filters the project's conventions findings feed down to this task", async () => {
    get.mockResolvedValue({ id: "t1", project_id: "proj-1" } as Task);
    const rows: ConventionFinding[] = [
      {
        file: "a.py",
        line: 1,
        rule: "no_routes_in_models",
        level: "block",
        kind: "route",
        message: "route in models",
        task_id: "t1",
        detected_at: "2026-09-01T00:00:00Z",
      },
      {
        file: "b.py",
        line: 2,
        rule: "no_routes_in_models",
        level: "block",
        kind: "route",
        message: "different task",
        task_id: "t2",
        detected_at: "2026-09-01T00:00:00Z",
      },
    ];
    conventionsFindings.mockResolvedValue(rows);

    const { result } = renderHook(() => useTaskConventionFindings("t1"), {
      wrapper,
    });

    await waitFor(() =>
      expect(conventionsFindings).toHaveBeenCalledWith("proj-1"),
    );
    await waitFor(() => expect(result.current.data).toHaveLength(1));
    expect(result.current.data[0].task_id).toBe("t1");
  });

  it("never calls the conventions endpoint for a project-less task", async () => {
    get.mockResolvedValue({ id: "t1", project_id: null } as Task);

    renderHook(() => useTaskConventionFindings("t1"), { wrapper });

    await waitFor(() => expect(get).toHaveBeenCalled());
    expect(conventionsFindings).not.toHaveBeenCalled();
  });
});

describe("usePrCiVerdict", () => {
  it("always reads unavailable — no backend endpoint exists (the no-CI-repo case)", () => {
    const { result } = renderHook(() => usePrCiVerdict("t1"), { wrapper });
    expect(result.current.isLoading).toBe(false);
    expect(result.current.data.available).toBe(false);
    expect(result.current.data.reason).toContain("no REST route");
  });
});
