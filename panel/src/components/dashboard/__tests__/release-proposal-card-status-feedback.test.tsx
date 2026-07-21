import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import type { ReleaseProposal } from "@/lib/api/release";
import { PageRefreshProvider } from "@/components/providers";

// Unlike release-proposal-card.test.tsx (which stubs useMutation entirely to
// test the query-failure/execute-status surfacing paths), these tests
// exercise the REAL useMutation onSuccess handler — mirrors the
// x-post-queue/video-post-queue/roadmap-review-queue test pattern — so
// approve()'s per-status toast copy is actually asserted.
const { resolveApproveRef } = vi.hoisted(() => ({
  resolveApproveRef: { current: null as null | ((v: unknown) => void) },
}));

const { getProposal, approve, reject } = vi.hoisted(() => ({
  getProposal: vi.fn(
    async (): Promise<ReleaseProposal> => ({
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
    }),
  ),
  // Deferred so the test can freeze the approve mid-flight.
  approve: vi.fn(
    () =>
      new Promise((r) => {
        resolveApproveRef.current = r as (v: unknown) => void;
      }),
  ),
  reject: vi.fn(async () => ({})),
}));

vi.mock("@/lib/api", () => ({
  releaseApi: { getProposal, approve, reject },
}));

const { toast } = vi.hoisted(() => ({
  toast: { success: vi.fn(), warning: vi.fn(), info: vi.fn(), error: vi.fn() },
}));
vi.mock("sonner", () => ({ toast }));

import { ReleaseProposalCard } from "../release-proposal-card";

function withProviders(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return (
    <QueryClientProvider client={client}>
      <PageRefreshProvider>{ui}</PageRefreshProvider>
    </QueryClientProvider>
  );
}

async function clickApprove() {
  render(withProviders(<ReleaseProposalCard />));
  fireEvent.click(
    await screen.findByRole("button", { name: /Approve & publish/i }),
  );
  // Radix marks the rest of the page inert/aria-hidden once the dialog opens
  // — the main card's own button drops out of the accessible tree, so this
  // now uniquely matches the dialog's confirm button.
  await screen.findByRole("dialog");
  fireEvent.click(
    await screen.findByRole("button", { name: /Approve & publish/i }),
  );
  await waitFor(() => expect(approve).toHaveBeenCalled());
}

describe("ReleaseProposalCard — status feedback (silent-bug-sweep #c)", () => {
  beforeEach(() => {
    getProposal.mockClear();
    approve.mockClear();
    reject.mockClear();
    toast.success.mockClear();
    toast.warning.mockClear();
    toast.info.mockClear();
    toast.error.mockClear();
    resolveApproveRef.current = null;
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it.each([
    [
      "already_in_progress",
      "A release execute is already in progress for this proposal.",
    ],
    [
      "redis_unavailable",
      "Redis is unavailable — can't acquire the release mutex.",
    ],
    ["lock_lost", "The release lock was lost mid-execute — retry the approve."],
    ["gate_failed", "Release halted (gate_failed): the gate is red"],
  ])("shows distinct feedback for the %s status", async (status, message) => {
    await clickApprove();

    resolveApproveRef.current?.({
      status,
      version: "0.14.0",
      files_changed: [],
      commit_sha: null,
      release_url: null,
      detail: "the gate is red",
    });

    await waitFor(() => expect(toast.warning).toHaveBeenCalledWith(message));
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("shows a success toast for the published status", async () => {
    await clickApprove();

    resolveApproveRef.current?.({
      status: "published",
      version: "0.14.0",
      files_changed: ["pyproject.toml"],
      commit_sha: "abc123",
      release_url: "https://github.com/example/example/releases/tag/v0.14.0",
      detail: "published",
    });

    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith("Published v0.14.0"),
    );
  });

  it("shows an info toast for the accepted (background-dispatched) status", async () => {
    await clickApprove();

    resolveApproveRef.current?.({
      status: "accepted",
      version: "0.14.0",
      files_changed: [],
      commit_sha: null,
      release_url: null,
      detail: "dispatched",
    });

    await waitFor(() =>
      expect(toast.info).toHaveBeenCalledWith(
        "Release execute dispatched — running in the background. This card updates as it progresses.",
      ),
    );
  });
});
