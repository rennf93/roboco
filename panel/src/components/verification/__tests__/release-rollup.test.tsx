import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import type {
  AcVerificationStamp,
  PrCiVerdict,
  ReviewerChainEntry,
  ReviewRoundFindings,
} from "@/lib/api/verification";
import type { ConventionFinding } from "@/lib/api/conventions";

const {
  useAcVerificationStamps,
  useFindingsByRound,
  usePrCiVerdict,
  useReviewerChain,
  useTaskConventionFindings,
} = vi.hoisted(() => ({
  useAcVerificationStamps: vi.fn(),
  useFindingsByRound: vi.fn(),
  usePrCiVerdict: vi.fn(),
  useReviewerChain: vi.fn(),
  useTaskConventionFindings: vi.fn(),
}));

vi.mock("@/hooks/use-verification", () => ({
  useAcVerificationStamps,
  useFindingsByRound,
  usePrCiVerdict,
  useReviewerChain,
  useTaskConventionFindings,
}));

import { ReleaseVerificationRollup } from "../release-rollup";

// Two member tasks with deliberately different state: "t1" fully verified
// with a clean CI verdict, "t2" partially verified with an open finding and
// an unavailable ("no-CI-repo") CI verdict — exercising the mixed
// verified/open aggregation across member tasks in one render.
const AC_BY_TASK: Record<string, AcVerificationStamp[]> = {
  t1: [{ criterion: "Criterion A", verified: true, evidence: "file.ts:12" }],
  t2: [
    { criterion: "Criterion B", verified: true, evidence: "commit abc" },
    { criterion: "Criterion C", verified: false, evidence: null },
  ],
};

const FINDINGS_BY_TASK: Record<string, ReviewRoundFindings[]> = {
  t1: [],
  t2: [
    {
      round: 1,
      origin: "qa",
      findings: [
        {
          id: "f1",
          task_id: "t2",
          origin: "qa",
          round: 1,
          author_slug: "fe-qa",
          file: "src/foo.ts",
          line: 42,
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
    },
  ],
};

const CI_BY_TASK: Record<string, PrCiVerdict> = {
  t1: { available: false, reason: "no route" },
  // t2 is the deliberate no-CI-repo case with its own distinct reason.
  t2: { available: false, reason: "no REST route exists for this repo" },
};

const CONVENTIONS_BY_TASK: Record<string, ConventionFinding[]> = {
  t1: [],
  t2: [],
};

const REVIEWERS_BY_TASK: Record<string, ReviewerChainEntry[]> = {
  t1: [{ round: 1, origin: "qa", author_slug: "fe-qa", model: null }],
  t2: [{ round: 1, origin: "qa", author_slug: "fe-qa", model: null }],
};

function setup() {
  useAcVerificationStamps.mockImplementation((taskId: string) => ({
    data: AC_BY_TASK[taskId] ?? [],
    isLoading: false,
  }));
  useFindingsByRound.mockImplementation((taskId: string) => ({
    data: FINDINGS_BY_TASK[taskId] ?? [],
    isLoading: false,
  }));
  usePrCiVerdict.mockImplementation((taskId: string) => ({
    data: CI_BY_TASK[taskId] ?? { available: false, reason: "unknown" },
    isLoading: false,
  }));
  useTaskConventionFindings.mockImplementation((taskId: string) => ({
    data: CONVENTIONS_BY_TASK[taskId] ?? [],
    isLoading: false,
  }));
  useReviewerChain.mockImplementation((taskId: string) => ({
    data: REVIEWERS_BY_TASK[taskId] ?? [],
    isLoading: false,
  }));
}

describe("ReleaseVerificationRollup", () => {
  it("reports the member-task set as unavailable when no ids are given", () => {
    render(<ReleaseVerificationRollup taskIds={[]} />);
    expect(
      screen.getByText(/does not expose|member task set/i),
    ).toBeInTheDocument();
    expect(useAcVerificationStamps).not.toHaveBeenCalled();
  });

  it("aggregates mixed verified/open state across member tasks, and surfaces the no-CI-repo case", () => {
    setup();
    render(
      <ReleaseVerificationRollup
        taskIds={["t1", "t2"]}
        taskTitles={{ t1: "Shared data layer", t2: "Verification tab" }}
      />,
    );

    // Fully-verified member: 1/1 AC, no open-findings badge.
    const row1 = screen.getByRole("button", {
      name: /Shared data layer/i,
    });
    expect(within(row1).getByText("1/1 AC verified")).toBeInTheDocument();
    expect(within(row1).queryByText(/open$/)).not.toBeInTheDocument();

    // Partially-verified member: 1/2 AC, one open finding, distinct CI
    // unavailable reason (the no-CI-repo member case).
    const row2 = screen.getByRole("button", {
      name: /Verification tab/i,
    });
    expect(within(row2).getByText("1/2 AC verified")).toBeInTheDocument();
    expect(within(row2).getByText("1 open")).toBeInTheDocument();

    // Both rows render their own CI-unavailable badge (each hook call is
    // keyed by its own task id — not one shared/global verdict).
    expect(screen.getAllByText("CI verdict unavailable")).toHaveLength(2);

    // Detail sections render per member (default-open collapsible), showing
    // the mixed AC/finding state side by side in one place.
    expect(screen.getByText("Criterion A")).toBeInTheDocument();
    expect(screen.getByText("Criterion B")).toBeInTheDocument();
    expect(screen.getByText("Criterion C")).toBeInTheDocument();
    expect(screen.getByText("major")).toBeInTheDocument();
    expect(screen.getByText("open")).toBeInTheDocument();
  });

  it("falls back to the bare task id when no title is supplied", () => {
    setup();
    render(<ReleaseVerificationRollup taskIds={["t1"]} />);
    expect(screen.getByRole("button", { name: /t1/i })).toBeInTheDocument();
  });
});
