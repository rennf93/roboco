import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import type { Playbook } from "@/lib/api/playbooks";

const { resolveApproveRef } = vi.hoisted(() => ({
  resolveApproveRef: { current: null as null | ((v: unknown) => void) },
}));

const { listDrafts, approve, reject } = vi.hoisted(() => ({
  listDrafts: vi.fn(
    async () =>
      [
        {
          id: "pb-1",
          title: "Recover a stuck claim lock",
          slug: "recover-claim-lock",
          problem: "an agent's claim TOCTOU wedges the task",
          procedure: "1. ...",
          tags: ["backend"],
          team: "backend",
          scope: "cell",
          status: "draft",
        },
        {
          id: "pb-2",
          title: "Rebase a behind-base branch",
          slug: "rebase-behind-base",
          problem: "the dev's branch fell behind master",
          procedure: "1. ...",
          tags: ["git"],
          team: "backend",
          scope: "cell",
          status: "draft",
        },
      ] as Playbook[],
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

vi.mock("@/lib/api", () => ({ playbooksApi: { listDrafts, approve, reject } }));

import { PlaybookReviewQueue } from "../playbook-review-queue";

function withQueryClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

describe("PlaybookReviewQueue — per-row disable during an approve (F084)", () => {
  beforeEach(() => {
    listDrafts.mockClear();
    approve.mockClear();
    reject.mockClear();
    resolveApproveRef.current = null;
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("disables only the playbook being approved, not every row's Approve", async () => {
    render(withQueryClient(<PlaybookReviewQueue />));

    const approveButtons = await screen.findAllByRole("button", {
      name: "Approve",
    });
    expect(approveButtons).toHaveLength(2);
    expect(approveButtons[0]).not.toBeDisabled();
    expect(approveButtons[1]).not.toBeDisabled();

    // Approve the first playbook — the mutation stays pending (deferred fn).
    fireEvent.click(approveButtons[0]);
    await waitFor(() => expect(approve).toHaveBeenCalledWith("pb-1"));

    // Row 1's Approve locks while its approve is in flight; row 2's Approve
    // stays usable so the reviewer can act on an independent playbook at the
    // same time. Before the fix every row shared `disabled={approveMutation.isPending}`.
    await waitFor(() => expect(approveButtons[0]).toBeDisabled());
    expect(approveButtons[1]).not.toBeDisabled();

    // Mutation resolves → row 1's Approve unlocks again.
    resolveApproveRef.current?.(undefined);
    await waitFor(() => expect(approveButtons[0]).not.toBeDisabled());
    expect(approveButtons[1]).not.toBeDisabled();
  });
});
