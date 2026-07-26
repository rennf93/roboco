import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import type { QualityReport } from "@/lib/api/sentinel";

const { resolveListRef } = vi.hoisted(() => ({
  resolveListRef: { current: null as null | ((v: unknown) => void) },
}));

function buildReport(overrides: Partial<QualityReport> = {}): QualityReport {
  return {
    task_id: "report-1",
    title: "Sentinel drift-watch cycle",
    completed_at: "2026-07-25T00:00:00+00:00",
    headline: "Waived findings climbed 3x this week",
    items: [
      {
        id: "item-0",
        area: "waivers",
        observation:
          "Minor findings in roboco/services/task.py keep getting waived",
        evidence: "5 waived-minor findings this week (prior week: 1)",
        suggested_action: "Convert to a Pest Control bug task",
        status: "proposed",
      },
    ],
    overall_assessment:
      "Drift is concentrated in one hotspot file, not systemic yet",
    ...overrides,
  };
}

const { listReports, approveItem, rejectItem } = vi.hoisted(() => ({
  listReports: vi.fn(
    () =>
      new Promise((r) => {
        resolveListRef.current = r as (v: unknown) => void;
      }),
  ),
  approveItem: vi.fn(async () => ({
    status: "approved",
    item_id: "item-0",
    materialized_task_id: "task-1",
    detail: "materialized as a Main-PM-owned task",
  })),
  rejectItem: vi.fn(async () => ({
    status: "rejected",
    item_id: "item-0",
    detail: "dismissed; feeds the next cycle's prompt",
  })),
}));

vi.mock("@/lib/api", () => ({
  sentinelApi: { listReports, approveItem, rejectItem },
}));

import { QualityReportsTab } from "../quality-reports-tab";

function withQueryClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

describe("QualityReportsTab", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resolveListRef.current = null;
  });

  it("shows a loading state before the list resolves", () => {
    render(withQueryClient(<QualityReportsTab />));
    expect(
      document.querySelectorAll('[data-slot="skeleton"]').length,
    ).toBeGreaterThan(0);
    expect(
      screen.queryByText(/Waived findings climbed/),
    ).not.toBeInTheDocument();
  });

  it("shows an empty state when no reports have been filed", async () => {
    render(withQueryClient(<QualityReportsTab />));
    resolveListRef.current?.([]);
    expect(
      await screen.findByText(/No quality reports filed yet/),
    ).toBeInTheDocument();
  });

  it("renders a report's headline, items, and overall assessment", async () => {
    render(withQueryClient(<QualityReportsTab />));
    resolveListRef.current?.([buildReport()]);

    expect(
      await screen.findByText("Waived findings climbed 3x this week"),
    ).toBeInTheDocument();
    expect(screen.getByText("waivers")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Minor findings in roboco/services/task.py keep getting waived",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/5 waived-minor findings this week/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Convert to a Pest Control bug task/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Drift is concentrated in one hotspot file, not systemic yet",
      ),
    ).toBeInTheDocument();
  });

  // Defect 2 fix: a proposed drift item now gets its own approve/dismiss
  // action even though the report itself stays a read-only report — was
  // "renders no approve/reject controls".
  it("renders per-item approve/dismiss controls for a proposed item", async () => {
    render(withQueryClient(<QualityReportsTab />));
    resolveListRef.current?.([buildReport()]);

    await screen.findByText("Waived findings climbed 3x this week");
    expect(
      screen.getByRole("button", { name: /approve/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /dismiss/i }),
    ).toBeInTheDocument();
  });

  it("hides the actions once an item is approved", async () => {
    render(withQueryClient(<QualityReportsTab />));
    resolveListRef.current?.([
      buildReport({
        items: [
          {
            id: "item-0",
            area: "waivers",
            observation: "Minor findings keep getting waived",
            evidence: "5 waived this week",
            suggested_action: "Convert to a Pest Control bug task",
            status: "approved",
            materialized_task_id: "task-1",
          },
        ],
      }),
    ]);

    await screen.findByText("Waived findings climbed 3x this week");
    expect(
      screen.queryByRole("button", { name: /approve/i }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("Approved")).toBeInTheDocument();
  });

  it("approves an item via the API", async () => {
    const user = userEvent.setup();
    render(withQueryClient(<QualityReportsTab />));
    resolveListRef.current?.([buildReport()]);

    await screen.findByText("Waived findings climbed 3x this week");
    await user.click(screen.getByRole("button", { name: /approve/i }));

    await waitFor(() =>
      expect(approveItem).toHaveBeenCalledWith("report-1", "item-0"),
    );
  });

  it("dismisses an item with a reason via the dialog", async () => {
    const user = userEvent.setup();
    render(withQueryClient(<QualityReportsTab />));
    resolveListRef.current?.([buildReport()]);

    await screen.findByText("Waived findings climbed 3x this week");
    await user.click(screen.getByRole("button", { name: /dismiss/i }));

    const dialog = await screen.findByRole("dialog");
    await user.type(screen.getByLabelText("Reason"), "already tracked");
    await user.click(
      within(dialog).getByRole("button", { name: /^dismiss$/i }),
    );

    await waitFor(() =>
      expect(rejectItem).toHaveBeenCalledWith(
        "report-1",
        "item-0",
        "already tracked",
      ),
    );
  });

  it("omits the overall-assessment section when absent", async () => {
    render(withQueryClient(<QualityReportsTab />));
    resolveListRef.current?.([buildReport({ overall_assessment: "" })]);

    await screen.findByText("Waived findings climbed 3x this week");
    expect(screen.queryByText("Overall assessment")).not.toBeInTheDocument();
  });
});
