import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import type { ReleaseProposal } from "@/lib/api/release";

// Control useQuery per test; the mutation + queryClient hooks just need to exist.
const { mockUseQuery } = vi.hoisted(() => ({
  mockUseQuery: vi.fn(),
}));

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-query")>();
  return {
    ...actual,
    useQuery: mockUseQuery,
    useMutation: vi.fn(() => ({ mutate: vi.fn(), mutateAsync: vi.fn() })),
    useQueryClient: vi.fn(() => ({
      invalidateQueries: vi.fn(),
    })),
  };
});

// releaseApi methods never run (useQuery/useMutation are mocked) — the
// component imports releaseApi from the barrel, so provide a stub object.
vi.mock("@/lib/api", () => ({
  releaseApi: {
    getProposal: vi.fn(),
    approve: vi.fn(),
    reject: vi.fn(),
  },
}));

import { ReleaseProposalCard } from "../release-proposal-card";
import { PageRefreshProvider } from "@/components/providers";

function withPageRefresh(ui: ReactNode) {
  return <PageRefreshProvider>{ui}</PageRefreshProvider>;
}

function buildProposal(): ReleaseProposal {
  return {
    task_id: "t1",
    title: "Cut v0.14.0",
    status: "awaiting_ceo_approval",
    required_changes: null,
    report: {
      proposed_version: "0.14.0",
      bump_kind: "minor",
      change_summary: ["feat: metrics"],
      drafted_changelog: "## 0.14.0\n- metrics",
      version_bump_plan: ["pyproject.toml"],
      gaps: [],
      migration_notes: [],
      gate_state: "green",
    },
  };
}

describe("ReleaseProposalCard — query-failure surfacing (F082)", () => {
  beforeEach(() => {
    mockUseQuery.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
  });

  it("surfaces a backend error (not a 404) instead of silently hiding", () => {
    // A non-404 failure (500, network drop) rethrows in releaseApi.getProposal,
    // so useQuery sees isError=true + data=undefined. Before the fix the card
    // collapsed this onto `!proposal` and returned null — the CEO had no idea
    // the release-proposal endpoint was unreachable.
    mockUseQuery.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("release service unavailable"),
      refetch: vi.fn(),
    });

    render(withPageRefresh(<ReleaseProposalCard />));

    // The failure must be visible — not a silent hide. The error card surfaces
    // the underlying message. Refresh is now handled by the navbar refresh button.
    expect(
      screen.getByText(/couldn't load the release proposal/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/release service unavailable/i),
    ).toBeInTheDocument();
  });

  it("still hides on the 404 no-open-proposal empty state (regression guard)", () => {
    // 404 → releaseApi.getProposal returns null → data=null, isError=false.
    // That's the normal empty state and must stay hidden (mirrors PrReviewQueue).
    mockUseQuery.mockReturnValue({
      data: null,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    const { container } = render(withPageRefresh(<ReleaseProposalCard />));
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the proposal card on the happy path (regression guard)", () => {
    mockUseQuery.mockReturnValue({
      data: buildProposal(),
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(withPageRefresh(<ReleaseProposalCard />));
    expect(screen.getByText(/Release Proposal/i)).toBeInTheDocument();
    expect(screen.getByText("v0.14.0")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Approve & publish/i }),
    ).toBeInTheDocument();
  });
});

describe("ReleaseProposalCard — execute outcome surfacing (W8b)", () => {
  it("renders the in-flight badge and disables actions while execute runs", () => {
    // execute_in_flight is the UX for the Redis-mutex-protected background
    // execute; the approve/reject buttons disable so the CEO can't double-click.
    mockUseQuery.mockReturnValue({
      data: { ...buildProposal(), execute_in_flight: true },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(withPageRefresh(<ReleaseProposalCard />));

    expect(
      screen.getByText(/release execute running in the background/i),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Reject with changes/i }),
    ).toBeDisabled();
    // Approve stays present but disabled while the execute runs.
    const approve = screen.getByRole("button", { name: /Approve & publish/i });
    expect(approve).toBeDisabled();
  });

  it("renders the failure block and a Retry label from a persisted execute_status", () => {
    // A failed ~40min execute left the proposal open with a persisted
    // execute_status; the card surfaces the reason and flips Approve to Retry.
    mockUseQuery.mockReturnValue({
      data: {
        ...buildProposal(),
        execute_status: "gate_failed",
        execute_detail: "make quality failed",
      },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(withPageRefresh(<ReleaseProposalCard />));

    expect(screen.getByText(/last execute failed/i)).toBeInTheDocument();
    expect(screen.getByText(/make quality failed/i)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Retry approve & publish/i }),
    ).toBeInTheDocument();
  });
});
