import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import type { SpackleCycle } from "@/lib/api/spackle";

const { resolveApproveRef } = vi.hoisted(() => ({
  resolveApproveRef: { current: null as null | ((v: unknown) => void) },
}));

const { listCycles, approveItem, rejectItem } = vi.hoisted(() => ({
  listCycles: vi.fn(
    async () =>
      [
        {
          task_id: "cycle-1",
          title: "Spackle exploration cycle",
          status: "pending",
          items: [
            {
              id: "item-0",
              title: "Add panel surface for /api/widgets",
              description: "The route exists but no panel page calls it",
              acceptance_criteria: ["panel page lists widgets"],
              project_slug: "backend-svc",
              team: "backend",
              priority: 2,
              evidence:
                "roboco/api/routes/widgets.py:12 defines GET /widgets; no panel/src page calls it",
              status: "proposed",
            },
            {
              id: "item-1",
              title: "Document ROBOCO_WIDGETS_ENABLED",
              description: "Flag is armed in config.py but undocumented",
              acceptance_criteria: ["docs/map entry added"],
              project_slug: "frontend-app",
              team: "frontend",
              priority: 3,
              evidence:
                "roboco/config.py:210 defines the flag; docs/map/ has no entry for it",
              status: "proposed",
            },
          ],
        },
      ] as SpackleCycle[],
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
  spackleApi: { listCycles, approveItem, rejectItem },
}));

const { toast } = vi.hoisted(() => ({
  toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn() },
}));
vi.mock("sonner", () => ({ toast }));

import { SpackleReviewQueue } from "../spackle-review-queue";

function withQueryClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

describe("SpackleReviewQueue", () => {
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

  it("renders both item drafts with their evidence", async () => {
    render(withQueryClient(<SpackleReviewQueue />));
    expect(
      await screen.findByText("Add panel surface for /api/widgets"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Document ROBOCO_WIDGETS_ENABLED"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/roboco\/api\/routes\/widgets.py:12/),
    ).toBeInTheDocument();
  });

  it("disables only the item being approved, not every row's Approve", async () => {
    render(withQueryClient(<SpackleReviewQueue />));

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
    render(withQueryClient(<SpackleReviewQueue />));
    const rejectButtons = await screen.findAllByRole("button", {
      name: "Reject",
    });
    fireEvent.click(rejectButtons[1]);

    const reasonBox = await screen.findByLabelText("Reason");
    fireEvent.change(reasonBox, { target: { value: "already tracked" } });
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));

    await waitFor(() =>
      expect(rejectItem).toHaveBeenCalledWith(
        "cycle-1",
        "item-1",
        "already tracked",
      ),
    );
  });

  it("renders nothing when there is no authored cycle", async () => {
    listCycles.mockResolvedValueOnce([]);
    const { container } = render(withQueryClient(<SpackleReviewQueue />));
    await waitFor(() => expect(listCycles).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

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
      render(withQueryClient(<SpackleReviewQueue />));
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
