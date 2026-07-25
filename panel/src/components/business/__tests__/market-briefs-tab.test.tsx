import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import type { MarketBrief } from "@/lib/api/periscope";

const { resolveListRef } = vi.hoisted(() => ({
  resolveListRef: { current: null as null | ((v: unknown) => void) },
}));

function buildBrief(overrides: Partial<MarketBrief> = {}): MarketBrief {
  return {
    task_id: "brief-1",
    title: "Periscope market-research cycle",
    completed_at: "2026-07-25T00:00:00+00:00",
    headline: "A rival tool shipped agentic PR review",
    findings: [
      {
        id: "finding-0",
        claim: "Competitor X launched an autonomous PR-review agent",
        source_url: "https://example.com/competitor-x-launch",
        relevance: "Directly overlaps our pr_reviewer role",
      },
    ],
    threats: ["Feature parity gap on PR review"],
    opportunities: ["Lean into our findings-ledger differentiator"],
    positioning_note: "Emphasize the structured findings ledger in messaging",
    ...overrides,
  };
}

const { listBriefs } = vi.hoisted(() => ({
  listBriefs: vi.fn(
    () =>
      new Promise((r) => {
        resolveListRef.current = r as (v: unknown) => void;
      }),
  ),
}));

vi.mock("@/lib/api", () => ({
  periscopeApi: { listBriefs },
}));

import { MarketBriefsTab } from "../market-briefs-tab";

function withQueryClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

describe("MarketBriefsTab", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resolveListRef.current = null;
  });

  it("shows a loading state before the list resolves", () => {
    render(withQueryClient(<MarketBriefsTab />));
    expect(
      document.querySelectorAll('[data-slot="skeleton"]').length,
    ).toBeGreaterThan(0);
    expect(screen.queryByText(/A rival tool shipped/)).not.toBeInTheDocument();
  });

  it("shows an empty state when no briefs have been filed", async () => {
    render(withQueryClient(<MarketBriefsTab />));
    resolveListRef.current?.([]);
    expect(
      await screen.findByText(/No market briefs filed yet/),
    ).toBeInTheDocument();
  });

  it("renders a brief's headline, findings, threats, opportunities, and positioning note", async () => {
    render(withQueryClient(<MarketBriefsTab />));
    resolveListRef.current?.([buildBrief()]);

    expect(
      await screen.findByText("A rival tool shipped agentic PR review"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Competitor X launched an autonomous PR-review agent"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Directly overlaps our pr_reviewer role/),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "source" })).toHaveAttribute(
      "href",
      "https://example.com/competitor-x-launch",
    );
    expect(screen.getByText("Feature parity gap on PR review")).toBeInTheDocument();
    expect(
      screen.getByText("Lean into our findings-ledger differentiator"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Emphasize the structured findings ledger in messaging",
      ),
    ).toBeInTheDocument();
  });

  it("renders no approve/reject controls — a report has no queue action", async () => {
    render(withQueryClient(<MarketBriefsTab />));
    resolveListRef.current?.([buildBrief()]);

    await screen.findByText("A rival tool shipped agentic PR review");
    expect(
      screen.queryByRole("button", { name: /approve/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /reject/i }),
    ).not.toBeInTheDocument();
  });

  it("omits optional sections when threats/opportunities/positioning are absent", async () => {
    render(withQueryClient(<MarketBriefsTab />));
    resolveListRef.current?.([
      buildBrief({ threats: [], opportunities: [], positioning_note: "" }),
    ]);

    await screen.findByText("A rival tool shipped agentic PR review");
    expect(screen.queryByText("Threats")).not.toBeInTheDocument();
    expect(screen.queryByText("Opportunities")).not.toBeInTheDocument();
    expect(screen.queryByText("Positioning")).not.toBeInTheDocument();
  });
});
