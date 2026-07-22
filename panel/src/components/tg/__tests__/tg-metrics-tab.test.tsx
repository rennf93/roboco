import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TgMetricsTab } from "../tg-metrics-tab";

// A mutable ref (not a bare boolean) so a later describe block can flip it to
// false for the non-demo error-state tests without a second module factory.
const { demoMode } = vi.hoisted(() => ({ demoMode: { current: true } }));
vi.mock("@/lib/telegram/demo", () => ({ isTgDemoMode: () => demoMode.current }));
// Scorecard resolution needs the roster only outside demo mode (the demo
// scorecard fixture returns unconditionally) — an empty roster keeps this
// hook off the network without affecting anything the tests assert on.
vi.mock("@/hooks/use-agents", () => ({ useAgents: () => ({ data: [] }) }));

const usageMocks = vi.hoisted(() => ({
  getUsageSummary: vi.fn(),
  getUsageTimeSeries: vi.fn(),
  getAgentUsage: vi.fn(),
  getTeamUsage: vi.fn(),
  getModelUsage: vi.fn(),
  getCacheEfficiency: vi.fn(),
  getUsageProjection: vi.fn(),
  getSpawnWaste: vi.fn(),
}));
vi.mock("@/lib/api/usage", () => ({ usageApi: usageMocks }));

const observabilityMocks = vi.hoisted(() => ({
  getRework: vi.fn(),
  getCycleTime: vi.fn(),
}));
vi.mock("@/lib/api/observability", () => ({
  observabilityApi: observabilityMocks,
}));

beforeEach(() => {
  demoMode.current = true;
  for (const m of Object.values(usageMocks)) m.mockReset();
  for (const m of Object.values(observabilityMocks)) m.mockReset();
});

function renderTab() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <TgMetricsTab />
    </QueryClientProvider>,
  );
}

describe("TgMetricsTab", () => {
  it("renders every hub section from the demo fixtures", async () => {
    renderTab();

    // Hero total — the demo agent/team/model/series slices all sum to $66.54.
    expect(await screen.findByText("$66.54")).toBeInTheDocument();
    expect(screen.getByText("By agent")).toBeInTheDocument();
    expect(screen.getByText("By team")).toBeInTheDocument();
    expect(screen.getByText("By model")).toBeInTheDocument();
    expect(screen.getByText("Delivery")).toBeInTheDocument();
    expect(screen.getByText("Efficiency")).toBeInTheDocument();

    // Top agent by cost (be-dev-1, $18.42) renders first with its real name.
    expect(await screen.findByText("Backend Dev 1")).toBeInTheDocument();
    // A team row's exact label (distinct from "Backend Dev 1" above).
    expect(await screen.findByText("Backend")).toBeInTheDocument();
  });

  it("switches the selected period on the segmented control", async () => {
    renderTab();
    await screen.findByText("$66.54");

    const oneWeek = screen.getByRole("button", { name: "1W" });
    const oneMonth = screen.getByRole("button", { name: "1M" });
    expect(oneWeek).toHaveAttribute("aria-pressed", "true");
    expect(oneMonth).toHaveAttribute("aria-pressed", "false");

    await userEvent.click(oneMonth);

    expect(oneMonth).toHaveAttribute("aria-pressed", "true");
    expect(oneWeek).toHaveAttribute("aria-pressed", "false");
  });

  it("pushes the agent drilldown when a by-agent row is tapped", async () => {
    renderTab();
    await screen.findByText("$66.54");

    await userEvent.click(await screen.findByText("Backend Dev 1"));

    expect(
      await screen.findByRole("heading", { name: "Backend Dev 1" }),
    ).toBeInTheDocument();
    expect(screen.getByText("be-dev-1")).toBeInTheDocument();
  });

  it("shows the drilled-in agent's scorecard from the demo fixture", async () => {
    renderTab();
    await screen.findByText("$66.54");
    await userEvent.click(await screen.findByText("Backend Dev 1"));

    expect(await screen.findByText("Scorecard")).toBeInTheDocument();
    expect(screen.getByText("14")).toBeInTheDocument(); // tasks_completed
  });

  it("returns to the hub from the drilldown's back button", async () => {
    renderTab();
    await screen.findByText("$66.54");
    await userEvent.click(await screen.findByText("Backend Dev 1"));
    await screen.findByRole("heading", { name: "Backend Dev 1" });

    await userEvent.click(screen.getByRole("button", { name: "Back" }));

    expect(await screen.findByText("By agent")).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Backend Dev 1" }),
    ).not.toBeInTheDocument();
  });
});

describe("TgMetricsTab — section error states (non-demo)", () => {
  beforeEach(() => {
    demoMode.current = false;
    usageMocks.getUsageSummary.mockResolvedValue({
      tokens_input: 0,
      tokens_output: 0,
      total_tokens: 1000,
      total_cost_usd: 10,
      trend_pct: 0,
      period: "7d",
    });
    usageMocks.getUsageTimeSeries.mockResolvedValue([]);
    usageMocks.getAgentUsage.mockResolvedValue([]);
    usageMocks.getTeamUsage.mockResolvedValue([]);
    usageMocks.getModelUsage.mockResolvedValue([]);
    usageMocks.getCacheEfficiency.mockResolvedValue({
      cache_hit_rate: 0.42,
      tokens_cache_read: 0,
      tokens_cache_write: 0,
      tokens_input: 0,
      cost_saved_by_cache_usd: 3,
      period: "7d",
    });
    usageMocks.getUsageProjection.mockResolvedValue({
      total_cost_7d: 10,
      avg_daily_cost_usd: 1,
      projected_monthly_cost_usd: 30,
      basis_days: 7,
    });
    usageMocks.getSpawnWaste.mockResolvedValue({
      total_spawns: 10,
      unproductive_spawns: 1,
      unproductive_pct: 10,
      by_role: [],
      respawn_strikes: [],
      period: "7d",
    });
    // The Delivery section's own fetch fails while everything else succeeds.
    observabilityMocks.getRework.mockRejectedValue(new Error("network"));
    observabilityMocks.getCycleTime.mockResolvedValue([]);
  });

  it("shows an inline error note for Delivery instead of a 0%-backed rework tile", async () => {
    renderTab();

    expect(await screen.findByText("$10.00")).toBeInTheDocument();
    expect(
      await screen.findByText(/Couldn.t load delivery metrics/i),
    ).toBeInTheDocument();
    expect(screen.queryByText("Rework rate")).not.toBeInTheDocument();
    // Efficiency loaded fine — its own tiles still render real numbers.
    expect(screen.getByText("42%")).toBeInTheDocument();
  });
});
