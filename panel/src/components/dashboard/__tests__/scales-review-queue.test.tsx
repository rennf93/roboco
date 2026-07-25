import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import type { RebalanceCycle } from "@/lib/api/scales";

const { resolveApproveRef } = vi.hoisted(() => ({
  resolveApproveRef: { current: null as null | ((v: unknown) => void) },
}));

const { listCycles, approveItem, rejectItem } = vi.hoisted(() => ({
  listCycles: vi.fn(
    async () =>
      [
        {
          task_id: "cycle-1",
          title: "Scales portfolio-rebalance cycle",
          status: "pending",
          items: [
            {
              id: "item-0",
              task_ref: "abc12345",
              target_task_id: "11111111-1111-1111-1111-111111111111",
              target_task_title: "Stale onboarding polish task",
              action: "reprioritize",
              new_priority: 0,
              rationale: "Onboarding friction is this quarter's top charter goal",
              status: "proposed",
            },
            {
              id: "item-1",
              task_ref: "Old experimental widget",
              target_task_id: "22222222-2222-2222-2222-222222222222",
              target_task_title: "Old experimental widget",
              action: "cancel",
              new_priority: null,
              rationale: "Superseded by the new dashboard; no longer on the roadmap",
              status: "proposed",
            },
          ],
        },
      ] as RebalanceCycle[],
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
  scalesApi: { listCycles, approveItem, rejectItem },
}));

const { toast } = vi.hoisted(() => ({
  toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn() },
}));
vi.mock("sonner", () => ({ toast }));

import { ScalesReviewQueue } from "../scales-review-queue";

function withQueryClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

describe("ScalesReviewQueue", () => {
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

  it("renders both item drafts with their rationale and action", async () => {
    render(withQueryClient(<ScalesReviewQueue />));
    expect(
      await screen.findByText("Stale onboarding polish task"),
    ).toBeInTheDocument();
    expect(screen.getByText("Old experimental widget")).toBeInTheDocument();
    expect(
      screen.getByText(/Onboarding friction is this quarter's top charter goal/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Reprioritize/)).toBeInTheDocument();
    expect(screen.getByText("Cancel")).toBeInTheDocument();
  });

  it("disables only the item being approved, not every row's Approve", async () => {
    render(withQueryClient(<ScalesReviewQueue />));

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
      executed_detail: "priority changed to P0",
      detail: "priority changed to P0",
    });
    await waitFor(() => expect(approveButtons[0]).not.toBeDisabled());
  });

  it("rejects an item with a reason", async () => {
    render(withQueryClient(<ScalesReviewQueue />));
    const rejectButtons = await screen.findAllByRole("button", {
      name: "Reject",
    });
    fireEvent.click(rejectButtons[1]);

    const reasonBox = await screen.findByLabelText("Reason");
    fireEvent.change(reasonBox, { target: { value: "still needed" } });
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));

    await waitFor(() =>
      expect(rejectItem).toHaveBeenCalledWith(
        "cycle-1",
        "item-1",
        "still needed",
      ),
    );
  });

  it("renders nothing when there is no authored cycle", async () => {
    listCycles.mockResolvedValueOnce([]);
    const { container } = render(withQueryClient(<ScalesReviewQueue />));
    await waitFor(() => expect(listCycles).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it.each([
    [
      "already_approved",
      "this item was already approved",
      "this item was already approved",
    ],
    [
      "invalid_state",
      "item is 'rejected', not proposed — cannot approve",
      "item is 'rejected', not proposed — cannot approve",
    ],
  ])(
    "shows distinct feedback for the %s status",
    async (status, detail, message) => {
      render(withQueryClient(<ScalesReviewQueue />));
      const approveButtons = await screen.findAllByRole("button", {
        name: /Approve/,
      });
      fireEvent.click(approveButtons[0]);
      await waitFor(() => expect(approveItem).toHaveBeenCalled());

      resolveApproveRef.current?.({
        status,
        item_id: "item-0",
        executed_detail: null,
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
