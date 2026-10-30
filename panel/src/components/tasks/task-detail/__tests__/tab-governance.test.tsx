import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import React from "react";
import { TaskStatus, Team, TaskType, type Task } from "@/types";
import type { GovernanceReportResponse } from "@/types";

// Mirrors tab-findings.test.tsx's shape: mock the data hook directly rather
// than wiring a real QueryClient — TabGovernance only reads the hook's
// return value.
const { useTaskGovernance } = vi.hoisted(() => ({
  useTaskGovernance: vi.fn(),
}));

vi.mock("@/hooks/use-tasks", () => ({ useTaskGovernance }));

import { TabGovernance } from "../tab-governance";

function buildTask(overrides: Partial<Task> = {}): Task {
  return {
    id: "t1",
    title: "Task",
    description: "d",
    status: TaskStatus.AWAITING_QA,
    team: Team.BACKEND,
    task_type: TaskType.CODE,
    acceptance_criteria: [],
    ...overrides,
  } as unknown as Task;
}

function buildReport(
  overrides: Partial<GovernanceReportResponse> = {},
): GovernanceReportResponse {
  return {
    task_id: "t1",
    task_status: "awaiting_qa",
    revision_count: 1,
    gate_chain: [
      {
        gate: "conventions",
        status: "passed",
        timestamp: null,
        detail: "2 warn finding(s)",
      },
      {
        gate: "self_verification",
        status: "passed",
        timestamp: "2026-09-01T10:00:00Z",
        detail: null,
      },
      {
        gate: "qa",
        status: "failed",
        timestamp: "2026-09-02T11:30:00Z",
        detail: "bounced",
      },
      { gate: "pr_gate", status: "not_reached", timestamp: null, detail: null },
      {
        gate: "pm_review",
        status: "not_reached",
        timestamp: null,
        detail: null,
      },
      {
        gate: "ceo_approval",
        status: "not_reached",
        timestamp: null,
        detail: null,
      },
    ],
    findings_summary: [
      { origin: "qa", open: 1, addressed: 0, verified: 1, waived: 0 },
    ],
    conventions_block_count: 0,
    conventions_warn_count: 2,
    ...overrides,
  };
}

describe("TabGovernance", () => {
  it("renders the gate chain as a timeline with verdict badges", () => {
    useTaskGovernance.mockReturnValue({ data: buildReport(), isLoading: false });
    render(<TabGovernance task={buildTask()} />);

    // Every gate of the chain appears in the backend's order. Scoped to the
    // timeline <ol> so the Conventions metric card's label can't collide.
    const gates = within(screen.getByRole("list"))
      .getAllByText(
        /^(Conventions|Self-verification|QA|PR gate|PM review|CEO approval)$/,
      )
      .map((el) => el.textContent);
    expect(gates).toEqual([
      "Conventions",
      "Self-verification",
      "QA",
      "PR gate",
      "PM review",
      "CEO approval",
    ]);

    // Verdicts: one failed, two passed, three not reached.
    expect(screen.getByText("failed")).toBeInTheDocument();
    expect(screen.getAllByText("passed")).toHaveLength(2);
    expect(screen.getAllByText("not reached")).toHaveLength(3);
  });

  it("renders gate timestamps and detail lines", () => {
    useTaskGovernance.mockReturnValue({ data: buildReport(), isLoading: false });
    render(<TabGovernance task={buildTask()} />);

    // The QA bounce carries its detail line and an absolute timestamp.
    expect(screen.getByText("bounced")).toBeInTheDocument();
    expect(screen.getByText(/Sep 1, 2026/)).toBeInTheDocument();
    expect(screen.getByText(/Sep 2, 2026/)).toBeInTheDocument();
    // Untouched gates carry no timestamp text.
    expect(screen.getAllByText(/Sep \d/, { exact: false })).toHaveLength(2);
  });

  it("renders the revision count as a prominent metric", () => {
    useTaskGovernance.mockReturnValue({ data: buildReport(), isLoading: false });
    render(<TabGovernance task={buildTask()} />);

    expect(screen.getByText("1 revision")).toBeInTheDocument();
  });

  it("renders the conventions verdict from the block/warn counters", () => {
    useTaskGovernance.mockReturnValue({ data: buildReport(), isLoading: false });
    render(<TabGovernance task={buildTask()} />);

    expect(screen.getByText("2 warn findings")).toBeInTheDocument();
    expect(screen.queryByText("clean")).toBeNull();
  });

  it("renders a clean conventions verdict when no findings exist", () => {
    useTaskGovernance.mockReturnValue({
      data: buildReport({ conventions_block_count: 0, conventions_warn_count: 0 }),
      isLoading: false,
    });
    render(<TabGovernance task={buildTask()} />);

    expect(screen.getByText("clean")).toBeInTheDocument();
  });

  it("summarizes the findings ledger per origin", () => {
    useTaskGovernance.mockReturnValue({
      data: buildReport({
        findings_summary: [
          { origin: "qa", open: 1, addressed: 0, verified: 1, waived: 0 },
          { origin: "pr_gate", open: 0, addressed: 2, verified: 0, waived: 0 },
        ],
      }),
      isLoading: false,
    });
    render(<TabGovernance task={buildTask()} />);

    expect(screen.getByText("QA: 1 open · 1 closed")).toBeInTheDocument();
    expect(screen.getByText("PR Review: 0 open · 2 closed")).toBeInTheDocument();
  });

  it("shows the reported task status", () => {
    useTaskGovernance.mockReturnValue({ data: buildReport(), isLoading: false });
    render(<TabGovernance task={buildTask()} />);

    expect(screen.getByText("awaiting_qa")).toBeInTheDocument();
  });

  it("renders the empty state when no gate has been reached", () => {
    useTaskGovernance.mockReturnValue({
      data: buildReport({
        revision_count: 0,
        findings_summary: [],
        conventions_warn_count: 0,
        gate_chain: buildReport().gate_chain.map((s) => ({
          ...s,
          status: "not_reached",
          timestamp: null,
          detail: null,
        })),
      }),
      isLoading: false,
    });
    render(<TabGovernance task={buildTask()} />);

    expect(screen.getByText("No governance activity yet.")).toBeInTheDocument();
    expect(screen.queryByText("Quality-gate chain")).toBeNull();
  });

  it("renders an error state when the report fails to load", () => {
    useTaskGovernance.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
    });
    render(<TabGovernance task={buildTask()} />);

    expect(
      screen.getByText("Couldn't load the governance report."),
    ).toBeInTheDocument();
  });

  it("renders skeletons while loading", () => {
    useTaskGovernance.mockReturnValue({ data: undefined, isLoading: true });
    const { container } = render(<TabGovernance task={buildTask()} />);

    expect(screen.queryByText("Quality-gate chain")).toBeNull();
    // Skeletons are the only content rendered.
    expect(
      container.querySelectorAll('[data-slot="skeleton"]').length,
    ).toBeGreaterThan(0);
  });
});
