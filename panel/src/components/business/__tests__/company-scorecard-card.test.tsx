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
      screen.queryByText("Could not load scorecard data")
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
      screen.getByText("Could not load scorecard data")
    ).toBeInTheDocument();

    // Skeleton and scorecard body should not appear
    expect(screen.queryByText("Company Scorecard")).not.toBeInTheDocument();
  });

  it("renders OfflineState when data is undefined (no error flag)", () => {
    setQueryState({ isError: false, data: undefined });

    render(<CompanyScorecardCard />);

    // When data is falsy the component falls through to the OfflineState branch
    expect(
      screen.getByText("Could not load scorecard data")
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

    expect(screen.getByText("No data yet")).toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // AC2 Scenario 7: Speed — formatted value when lead time is present
  // -------------------------------------------------------------------------
  it("shows formatted lead time when median_lead_time_hours is present", () => {
    setQueryState({
      data: buildSummary({ median_lead_time_hours: 18.7 }),
    });

    render(<CompanyScorecardCard />);

    // Component renders `{value.toFixed(1)}h median — target: < 24h`
    expect(screen.getByText(/18\.7h/)).toBeInTheDocument();

    // 'No data yet' must NOT appear
    expect(screen.queryByText("No data yet")).not.toBeInTheDocument();
  });
});
