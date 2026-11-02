import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import type {
  AcVerificationStamp,
  MemberVerification,
  PrCiVerdict,
  ReviewerChainEntry,
  ReviewRoundFindings,
} from "@/lib/api/verification";
import type { ConventionFinding } from "@/lib/api/conventions";

const { useReleaseVerification, useReleaseCiVerdicts } = vi.hoisted(() => ({
  useReleaseVerification: vi.fn(),
  useReleaseCiVerdicts: vi.fn(),
}));

vi.mock("@/hooks/use-verification", () => ({
  useReleaseVerification,
  useReleaseCiVerdicts,
}));

import { ReleaseVerificationRollup } from "../release-rollup";

// Two member tasks with deliberately different state: "t1" fully verified
// with a clean CI verdict, "t2" partially verified with an open finding and
// an unavailable ("no-CI-repo") CI verdict — exercising the mixed
// verified/open aggregation across member tasks in one render.
const AC_BY_TASK: Record<string, AcVerificationStamp[]> = {
  t1: [
    {
      criterion: "Criterion A",
      matched: true,
      verified: true,
      unresolved: false,
      evidence: "file.ts:12",
    },
  ],
  t2: [
    {
      criterion: "Criterion B",
      matched: true,
      verified: true,
      unresolved: false,
      evidence: "commit abc",
    },
    {
      criterion: "Criterion C",
      matched: false,
      verified: false,
      unresolved: false,
      evidence: null,
    },
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
  t1: { available: false, reason: "No CI verdict yet." },
  // t2 has its own distinct reason, to tell the two rows' verdicts apart.
  t2: {
    available: false,
    reason: "The task's verification record couldn't be loaded.",
  },
};

const CONVENTIONS_BY_TASK: Record<string, ConventionFinding[]> = {
  t1: [],
  t2: [],
};

const REVIEWERS_BY_TASK: Record<string, ReviewerChainEntry[]> = {
  t1: [
    {
      round: 1,
      role: "qa",
      agent_slug: "fe-qa",
      model: "claude-sonnet-5-5",
      started_at: "2026-09-01T00:00:00Z",
    },
  ],
  t2: [
    {
      round: 1,
      role: "qa",
      agent_slug: "fe-qa",
      model: "claude-sonnet-5-5",
      started_at: "2026-09-01T00:00:00Z",
    },
  ],
};

const TITLES: Record<string, string> = {
  t1: "Shared data layer",
  t2: "Verification tab",
};

function member(taskId: string): MemberVerification {
  return {
    taskId,
    title: TITLES[taskId],
    ac: AC_BY_TASK[taskId],
    findings: FINDINGS_BY_TASK[taskId],
    conventions: CONVENTIONS_BY_TASK[taskId],
    reviewers: REVIEWERS_BY_TASK[taskId],
  };
}

function setup({
  receiptsFail = false,
  ciFail = false,
}: { receiptsFail?: boolean; ciFail?: boolean } = {}) {
  useReleaseVerification.mockReturnValue({
    data: receiptsFail ? undefined : [member("t1"), member("t2")],
    isLoading: false,
    isError: receiptsFail,
  });
  useReleaseCiVerdicts.mockReturnValue({
    data: ciFail ? undefined : CI_BY_TASK,
    isLoading: false,
    isError: ciFail,
  });
}

describe("ReleaseVerificationRollup", () => {
  it("states this release genuinely has no member tasks, and fetches nothing, when it has none", () => {
    setup();
    render(<ReleaseVerificationRollup proposalTaskId="p1" memberCount={0} />);
    expect(
      screen.getByText(
        "This release has no member tasks. There's nothing to verify per-task.",
      ),
    ).toBeInTheDocument();
    // Must read as a legitimate empty state, not the old unreachable-data
    // escalation framing.
    expect(
      screen.queryByText(/available yet|endpoint|escalate/i),
    ).not.toBeInTheDocument();
    expect(useReleaseVerification).toHaveBeenCalledWith("p1", false);
    expect(useReleaseCiVerdicts).toHaveBeenCalledWith("p1", false);
  });

  it("aggregates mixed verified/open state across member tasks from the one bulk read", () => {
    setup();
    render(<ReleaseVerificationRollup proposalTaskId="p1" memberCount={2} />);
    expect(useReleaseVerification).toHaveBeenCalledWith("p1", true);

    // Fully-verified member: 1/1 AC, no open-findings badge.
    const row1 = screen.getByRole("button", {
      name: /Shared data layer/i,
    });
    expect(within(row1).getByText("1/1 AC verified")).toBeInTheDocument();
    expect(within(row1).queryByText(/open$/)).not.toBeInTheDocument();

    // Partially-verified member: 1/2 AC, one open finding.
    const row2 = screen.getByRole("button", {
      name: /Verification tab/i,
    });
    expect(within(row2).getByText("1/2 AC verified")).toBeInTheDocument();
    expect(within(row2).getByText("1 open")).toBeInTheDocument();

    // Each row renders the CI verdict keyed by its own task id.
    expect(screen.getAllByText("CI verdict unavailable")).toHaveLength(2);

    // Detail sections render per member (default-open collapsible), showing
    // the mixed AC/finding state side by side in one place.
    expect(screen.getByText("Criterion A")).toBeInTheDocument();
    expect(screen.getByText("Criterion B")).toBeInTheDocument();
    expect(screen.getByText("Criterion C")).toBeInTheDocument();
    expect(screen.getByText("major")).toBeInTheDocument();
    expect(screen.getByText("open")).toBeInTheDocument();
  });

  it("says the receipts could not be loaded instead of rendering an empty rollup", () => {
    setup({ receiptsFail: true });
    render(<ReleaseVerificationRollup proposalTaskId="p1" memberCount={2} />);
    expect(
      screen.getByText(
        "The release's verification receipts couldn't be loaded.",
      ),
    ).toBeInTheDocument();
  });

  it("marks every member's CI verdict unreadable when the CI read fails", () => {
    setup({ ciFail: true });
    render(<ReleaseVerificationRollup proposalTaskId="p1" memberCount={2} />);
    expect(screen.getAllByText("CI verdict unavailable")).toHaveLength(2);
  });
});
