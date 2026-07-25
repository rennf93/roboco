import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
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
        observation: "Minor findings in roboco/services/task.py keep getting waived",
        evidence: "5 waived-minor findings this week (prior week: 1)",
        suggested_action: "Convert to a Pest Control bug task",
      },
    ],
    overall_assessment: "Drift is concentrated in one hotspot file, not systemic yet",
    ...overrides,
  };
}

const { listReports } = vi.hoisted(() => ({
  listReports: vi.fn(
    () =>
      new Promise((r) => {
        resolveListRef.current = r as (v: unknown) => void;
      }),
  ),
}));

vi.mock("@/lib/api", () => ({
  sentinelApi: { listReports },
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

  it("renders no approve/reject controls — a report has no queue action", async () => {
    render(withQueryClient(<QualityReportsTab />));
    resolveListRef.current?.([buildReport()]);

    await screen.findByText("Waived findings climbed 3x this week");
    expect(
      screen.queryByRole("button", { name: /approve/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /reject/i }),
    ).not.toBeInTheDocument();
  });

  it("omits the overall-assessment section when absent", async () => {
    render(withQueryClient(<QualityReportsTab />));
    resolveListRef.current?.([buildReport({ overall_assessment: "" })]);

    await screen.findByText("Waived findings climbed 3x this week");
    expect(screen.queryByText("Overall assessment")).not.toBeInTheDocument();
  });
});
