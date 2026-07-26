import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import type { CockpitSummary } from "@/lib/api/cockpit";

// ---------------------------------------------------------------------------
// Mock @tanstack/react-query so we can control useQuery return values.
// vi.hoisted() ensures the variable exists before vi.mock() is hoisted.
// ---------------------------------------------------------------------------

const { mockUseQuery } = vi.hoisted(() => ({
  mockUseQuery: vi.fn(),
}));

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-query")>();
  return {
    ...actual,
    useQuery: mockUseQuery,
  };
});

// Mock the cockpit API module so no real HTTP calls occur
vi.mock("@/lib/api/cockpit", () => ({
  cockpitApi: {
    summary: vi.fn(),
  },
}));

// The spend trend chart pulls its own series via useUsageTimeSeries — a
// hook-level mock (not the raw react-query one above, which only controls
// the single cockpit-summary useQuery call) so SpendTrendChart never sees
// the mocked CockpitSummary object where it expects an array.
vi.mock("@/hooks/use-usage", () => ({
  useUsageTimeSeries: () => ({
    data: [],
    isLoading: false,
  }),
}));

// ---------------------------------------------------------------------------
// Import component AFTER mocks are set up
// ---------------------------------------------------------------------------

import { CompanyScorecardCard } from "../company-scorecard-card";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function buildSummary(overrides: Partial<CockpitSummary> = {}): CockpitSummary {
  return {
    basis: "test",
    north_star: "Test north star",
    objectives: [],
    delivery: {
      task_counts: {},
      in_flight: 5,
      blocked: 2,
      awaiting_ceo: 1,
      completed_30d: 12,
    },
    spend: {
      spend_30d_usd: 42.5,
      projected_monthly_usd: null,
      monthly_budget_cap_usd: null,
      over_budget: false,
    },
    pending_pitches: 0,
    signals: [],
    median_lead_time_hours: null,
    first_pass_yield: null,
    escaped_defects: null,
    ...overrides,
  };
}

function setQueryState(state: {
  isLoading?: boolean;
  isError?: boolean;
  data?: CockpitSummary | undefined;
}) {
  mockUseQuery.mockReturnValue({
    data: state.data,
    isLoading: state.isLoading ?? false,
    isError: state.isError ?? false,
    refetch: vi.fn(),
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("CompanyScorecardCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // -------------------------------------------------------------------------
  // AC2 Scenario 1: Loading skeleton
  // -------------------------------------------------------------------------
  it("renders skeleton groups while loading", () => {
    setQueryState({ isLoading: true });

    const { container } = render(<CompanyScorecardCard />);

    // Skeleton elements use data-slot="skeleton"
    const skeletons = container.querySelectorAll('[data-slot="skeleton"]');
    expect(skeletons.length).toBeGreaterThan(0);

    // Should NOT show error or data content
    expect(
      screen.queryByText("Could not load scorecard data"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Company Scorecard")).not.toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // AC2 Scenario 2: Error / OfflineState
  // -------------------------------------------------------------------------
  it("renders OfflineState when query errors", () => {
    setQueryState({ isError: true, data: undefined });

    render(<CompanyScorecardCard />);

    expect(
      screen.getByText("Could not load scorecard data"),
    ).toBeInTheDocument();

    // Skeleton and scorecard body should not appear
    expect(screen.queryByText("Company Scorecard")).not.toBeInTheDocument();
  });

  it("renders OfflineState when data is undefined (no error flag)", () => {
    setQueryState({ isError: false, data: undefined });

    render(<CompanyScorecardCard />);

    // When data is falsy the component falls through to the OfflineState branch
    expect(
      screen.getByText("Could not load scorecard data"),
    ).toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // AC2 Scenario 3: Delivery counts from mock data
  // -------------------------------------------------------------------------
  it("shows delivery counts from mock data", () => {
    setQueryState({
      data: buildSummary({
        delivery: {
          task_counts: {},
          in_flight: 7,
          blocked: 3,
          awaiting_ceo: 2,
          completed_30d: 15,
        },
      }),
    });

    render(<CompanyScorecardCard />);

    // All four metric values must appear
    expect(screen.getByText("7")).toBeInTheDocument(); // in_flight
    expect(screen.getByText("3")).toBeInTheDocument(); // blocked
    expect(screen.getByText("2")).toBeInTheDocument(); // awaiting_ceo
    expect(screen.getByText("15")).toBeInTheDocument(); // completed_30d

    // Labels
    expect(screen.getByText("In flight")).toBeInTheDocument();
    expect(screen.getByText("Blocked")).toBeInTheDocument();
    expect(screen.getByText("Awaiting CEO")).toBeInTheDocument();
    expect(screen.getByText("Done (30 d)")).toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // The spend-trend chart is wired into the Spend section
  // -------------------------------------------------------------------------
  it("renders the daily spend trend chart alongside the spend summary", () => {
    setQueryState({ data: buildSummary() });

    render(<CompanyScorecardCard />);

    expect(screen.getByText("Daily Spend (30d)")).toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // AC2 Scenario 4: Spend — 'No budget cap set' when cap is null
  // -------------------------------------------------------------------------
  it("shows 'No budget cap set' when monthly_budget_cap_usd is null", () => {
    setQueryState({
      data: buildSummary({
        spend: {
          spend_30d_usd: 10.0,
          projected_monthly_usd: null,
          monthly_budget_cap_usd: null,
          over_budget: false,
        },
      }),
    });

    render(<CompanyScorecardCard />);

    expect(screen.getByText("No budget cap set")).toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // AC2 Scenario 5: Spend — destructive styling when cap is set and over_budget
  // -------------------------------------------------------------------------
  it("applies destructive styling when cap is non-null and over_budget is true", () => {
    setQueryState({
      data: buildSummary({
        spend: {
          spend_30d_usd: 200.0,
          projected_monthly_usd: 220.0,
          monthly_budget_cap_usd: 150.0,
          over_budget: true,
        },
      }),
    });

    render(<CompanyScorecardCard />);

    // The cap value element should carry text-destructive class
    const capElement = screen.getByText(/\$150\.00/);
    expect(capElement).toHaveClass("text-destructive");

    // Over-budget indicator text is also visible
    expect(screen.getByText("(over budget)")).toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // AC2 Scenario 6: Speed — 'No data yet' when lead time is null
  // -------------------------------------------------------------------------
  it("shows 'No data yet' when median_lead_time_hours is null", () => {
    setQueryState({
      data: buildSummary({ median_lead_time_hours: null }),
    });

    render(<CompanyScorecardCard />);

    // Speed section renders 'No data yet' (and so do the objective cards
    // for the still-absent first_pass_yield / escaped_defects metrics).
    expect(screen.getAllByText("No data yet").length).toBeGreaterThan(0);
  });

  // -------------------------------------------------------------------------
  // AC2 Scenario 7: Speed — formatted value when lead time is present
  // -------------------------------------------------------------------------
  it("shows formatted lead time when median_lead_time_hours is present", () => {
    setQueryState({
      data: buildSummary({
        median_lead_time_hours: 18.7,
        first_pass_yield: 0.9,
        escaped_defects: 0,
      }),
    });

    render(<CompanyScorecardCard />);

    // Lead time renders in both the Speed section and the Objectives section
    expect(screen.getAllByText(/18\.7h/).length).toBeGreaterThanOrEqual(1);

    // 'No data yet' must NOT appear when all metrics are present
    expect(screen.queryByText("No data yet")).not.toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // Objectives section: three charter objective cards against live metrics
  // -------------------------------------------------------------------------

  it("renders three objective cards with target values when all metrics are present", () => {
    setQueryState({
      data: buildSummary({
        first_pass_yield: 0.92,
        median_lead_time_hours: 18.7,
        escaped_defects: 0,
        objectives: [
          { metric: "Tasks shipped to merge with no human code edits", target: "90%", status: "Active" },
          { metric: "Median lead time, intake → merged", target: "< 24h", status: "Active" },
          { metric: "Critical escaped defects per release", target: "0", status: "Active" },
        ],
      }),
    });

    render(<CompanyScorecardCard />);

    // Three objective labels from the charter objectives array
    expect(
      screen.getByText("Tasks shipped to merge with no human code edits"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Median lead time, intake → merged"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Critical escaped defects per release"),
    ).toBeInTheDocument();

    // Live metric values: first_pass_yield as a percentage (0.92 → 92%),
    // median_lead_time_hours as {value}h, escaped_defects as a count.
    expect(screen.getByText("92%")).toBeInTheDocument();
    expect(screen.getByText("18.7h")).toBeInTheDocument();
    expect(screen.getByText("0")).toBeInTheDocument();

    // All three target values render. The '< 24h' target appears in both the
    // Speed section and the Objectives section, so use getAllByText there.
    expect(screen.getByText(/target: 90%/)).toBeInTheDocument();
    expect(screen.getAllByText(/target: < 24h/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/target: 0/)).toBeInTheDocument();
  });

  it("renders 'No data yet' for missing first_pass_yield and escaped_defects, not fabricated labels", () => {
    setQueryState({
      data: buildSummary({
        first_pass_yield: null,
        median_lead_time_hours: 18.7,
        escaped_defects: undefined,
        objectives: [
          { metric: "Tasks shipped to merge with no human code edits", target: "90%", status: "Active" },
          { metric: "Median lead time, intake → merged", target: "< 24h", status: "Active" },
          { metric: "Critical escaped defects per release", target: "0", status: "Active" },
        ],
      }),
    });

    render(<CompanyScorecardCard />);

    // Two 'No data yet' fallbacks: one for first_pass_yield, one for escaped_defects.
    // median_lead_time_hours is present so its card shows the value, not the fallback.
    expect(screen.getAllByText("No data yet").length).toBe(2);

    // The fake stub labels must NOT appear anywhere
    expect(screen.queryByText("Revenue growth")).not.toBeInTheDocument();
    expect(screen.queryByText("Customer retention")).not.toBeInTheDocument();
    expect(screen.queryByText("Not tracked yet")).not.toBeInTheDocument();
  });

  it("does not render the fake 'Revenue growth' or 'Customer retention' stub labels", () => {
    setQueryState({
      data: buildSummary({
        first_pass_yield: 0.9,
        median_lead_time_hours: 12,
        escaped_defects: 0,
      }),
    });

    render(<CompanyScorecardCard />);

    expect(screen.queryByText("Revenue growth")).not.toBeInTheDocument();
    expect(screen.queryByText("Customer retention")).not.toBeInTheDocument();
    expect(screen.queryByText("Not tracked yet")).not.toBeInTheDocument();
  });
});
