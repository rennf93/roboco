import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import type { RoadmapCycle } from "@/lib/api/roadmap";

const { resolveApproveRef } = vi.hoisted(() => ({
  resolveApproveRef: { current: null as null | ((v: unknown) => void) },
}));

const { listCycles, approveItem, rejectItem } = vi.hoisted(() => ({
  listCycles: vi.fn(
    async () =>
      [
        {
          task_id: "cycle-1",
          title: "Roadmap exploration cycle",
          status: "pending",
          goal: "Close onboarding friction",
          items: [
            {
              id: "item-0",
              title: "Streamline signup",
              description: "Cut the signup form from 8 fields to 3",
              acceptance_criteria: ["signup takes < 30s"],
              project_slug: "backend-svc",
              team: "backend",
              priority: 2,
              rationale: "signup drop-off is the top funnel leak",
              status: "proposed",
            },
            {
              id: "item-1",
              title: "Simplify pricing page",
              description: "Remove the three-tier confusion",
              acceptance_criteria: ["one clear CTA"],
              project_slug: "frontend-app",
              team: "frontend",
              priority: 3,
              rationale: "pricing page bounce rate is high",
              status: "proposed",
            },
          ],
        },
      ] as RoadmapCycle[],
  ),
  // Deferred so the test can freeze the approve mid-flight.
  approveItem: vi.fn(
    () =>
      new Promise((r) => {
        resolveApproveRef.current = r as (v: unknown) => void;
      }),
  ),
  rejectItem: vi.fn(async () => ({})),
}));

vi.mock("@/lib/api", () => ({
  roadmapApi: { listCycles, approveItem, rejectItem },
}));

const { toast } = vi.hoisted(() => ({
  toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn() },
}));
vi.mock("sonner", () => ({ toast }));

import { RoadmapReviewQueue } from "../roadmap-review-queue";

function withQueryClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

describe("RoadmapReviewQueue", () => {
  beforeEach(() => {
    listCycles.mockClear();
    approveItem.mockClear();
    rejectItem.mockClear();
    toast.success.mockClear();
    toast.warning.mockClear();
    toast.error.mockClear();
    resolveApproveRef.current = null;
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders the cycle goal and both item drafts", async () => {
    render(withQueryClient(<RoadmapReviewQueue />));
    expect(
      await screen.findByText("Close onboarding friction"),
    ).toBeInTheDocument();
    expect(screen.getByText("Streamline signup")).toBeInTheDocument();
    expect(screen.getByText("Simplify pricing page")).toBeInTheDocument();
  });

  it("disables only the item being approved, not every row's Approve", async () => {
    render(withQueryClient(<RoadmapReviewQueue />));

    const approveButtons = await screen.findAllByRole("button", {
      name: /Approve/,
    });
    expect(approveButtons).toHaveLength(2);
    expect(approveButtons[0]).not.toBeDisabled();
    expect(approveButtons[1]).not.toBeDisabled();

    fireEvent.click(approveButtons[0]);
    await waitFor(() =>
      expect(approveItem).toHaveBeenCalledWith("cycle-1", "item-0"),
    );

    await waitFor(() => expect(approveButtons[0]).toBeDisabled());
    expect(approveButtons[1]).not.toBeDisabled();

    resolveApproveRef.current?.({
      status: "approved",
      item_id: "item-0",
      materialized_task_id: "t-1",
      detail: "materialized into the backlog",
    });
    await waitFor(() => expect(approveButtons[0]).not.toBeDisabled());
  });

  it("rejects an item with a reason", async () => {
    render(withQueryClient(<RoadmapReviewQueue />));
    const rejectButtons = await screen.findAllByRole("button", {
      name: "Reject",
    });
    fireEvent.click(rejectButtons[1]);

    const reasonBox = await screen.findByLabelText("Reason");
    fireEvent.change(reasonBox, { target: { value: "not a priority" } });
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));

    await waitFor(() =>
      expect(rejectItem).toHaveBeenCalledWith(
        "cycle-1",
        "item-1",
        "not a priority",
      ),
    );
  });

  it("renders nothing when there is no authored cycle", async () => {
    listCycles.mockResolvedValueOnce([]);
    const { container } = render(withQueryClient(<RoadmapReviewQueue />));
    await waitFor(() => expect(listCycles).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  // F(silent-bug-sweep #c): RoadmapService uses its own disjoint status
  // vocabulary (approved/already_approved/invalid_state/rejected/
  // already_rejected) — none of the 7 named cross-queue statuses
  // (already_in_progress, redis_unavailable, lock_lost, post_failed,
  // posted_partial, no_platforms, no_credentials) ever reach this queue, so
  // this locks in distinct feedback for roadmap's own statuses instead.
  it.each([
    [
      "already_approved",
      "this item was already approved",
      "Item approved — added to the backlog",
    ],
    [
      "invalid_state",
      "item is 'rejected', not proposed — cannot approve",
      "item is 'rejected', not proposed — cannot approve",
    ],
  ])(
    "shows distinct feedback for the %s status",
    async (status, detail, message) => {
      render(withQueryClient(<RoadmapReviewQueue />));
      const approveButtons = await screen.findAllByRole("button", {
        name: /Approve/,
      });
      fireEvent.click(approveButtons[0]);
      await waitFor(() => expect(approveItem).toHaveBeenCalled());

      resolveApproveRef.current?.({
        status,
        item_id: "item-0",
        materialized_task_id: null,
        detail,
      });

      await waitFor(() => {
        if (status === "already_approved") {
          expect(toast.success).toHaveBeenCalledWith(message);
        } else {
          expect(toast.warning).toHaveBeenCalledWith(message);
        }
      });
    },
  );
});
