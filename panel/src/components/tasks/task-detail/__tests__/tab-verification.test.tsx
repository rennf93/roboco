import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { Task } from "@/types";
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

import { TabVerification } from "../tab-verification";

const task = { id: "t1" } as Task;

function setup(overrides: {
  ac?: AcVerificationStamp[];
  findings?: ReviewRoundFindings[];
  ci?: PrCiVerdict;
  conventions?: ConventionFinding[];
  reviewers?: ReviewerChainEntry[];
}) {
  useAcVerificationStamps.mockReturnValue({
    data: overrides.ac ?? [],
    isLoading: false,
  });
  useFindingsByRound.mockReturnValue({
    data: overrides.findings ?? [],
    isLoading: false,
  });
  usePrCiVerdict.mockReturnValue({
    data: overrides.ci ?? {
      available: false,
      reason: "no route",
      technicalDetail: "no route",
    },
    isLoading: false,
  });
  useTaskConventionFindings.mockReturnValue({
    data: overrides.conventions ?? [],
    isLoading: false,
  });
  useReviewerChain.mockReturnValue({
    data: overrides.reviewers ?? [],
    isLoading: false,
  });
}

describe("TabVerification", () => {
  it("renders every criterion as verified with its evidence line (all-verified state)", () => {
    setup({
      ac: [
        {
          criterion: "Criterion A",
          matched: true,
          verified: true,
          unresolved: false,
          evidence: "file.ts:12",
        },
        {
          criterion: "Criterion B",
          matched: true,
          verified: true,
          unresolved: false,
          evidence: "commit abc",
        },
      ],
    });

    render(<TabVerification task={task} />);

    expect(screen.getByText("Criterion A")).toBeInTheDocument();
    expect(screen.getByText("file.ts:12")).toBeInTheDocument();
    expect(screen.getByText("Criterion B")).toBeInTheDocument();
  });

  it("renders an open finding with its severity and status (open-findings state)", () => {
    setup({
      findings: [
        {
          round: 1,
          origin: "qa",
          findings: [
            {
              id: "f1",
              task_id: "t1",
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
    });

    render(<TabVerification task={task} />);

    expect(screen.getByText("major")).toBeInTheDocument();
    expect(screen.getByText("open")).toBeInTheDocument();
    expect(screen.getByText("src/foo.ts:42")).toBeInTheDocument();
  });

  it("renders the PR CI verdict as unavailable (the no-CI-repo case)", () => {
    setup({
      ci: {
        available: false,
        reason: "Per-task PR CI verification isn't available yet.",
        technicalDetail: "no REST route exists",
      },
    });

    render(<TabVerification task={task} />);

    expect(screen.getByText("CI verdict unavailable")).toBeInTheDocument();
  });
});
