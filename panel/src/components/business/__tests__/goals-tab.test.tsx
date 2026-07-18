import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { CompanyGoals } from "@/lib/api/company-goals";

// ---------------------------------------------------------------------------
// Mock @tanstack/react-query so we can control useQuery/useMutation return
// values without a real QueryClientProvider.
// ---------------------------------------------------------------------------

const { mockUseQuery, mockUseMutation, mockMutate } = vi.hoisted(() => ({
  mockUseQuery: vi.fn(),
  mockUseMutation: vi.fn(),
  mockMutate: vi.fn(),
}));

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-query")>();
  return {
    ...actual,
    useQuery: mockUseQuery,
    useMutation: mockUseMutation,
    useQueryClient: () => ({ invalidateQueries: vi.fn() }),
  };
});

vi.mock("@/lib/api/company-goals", () => ({
  companyGoalsApi: {
    get: vi.fn(),
    update: vi.fn(),
  },
}));

// ---------------------------------------------------------------------------
// Import component AFTER mocks are set up
// ---------------------------------------------------------------------------

import { GoalsTab } from "../goals-tab";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function buildGoals(overrides: Partial<CompanyGoals> = {}): CompanyGoals {
  return {
    north_star: "Ship things",
    objectives: [
      { metric: "Lead time", target: "< 24h", status: "Active" },
      { metric: "Escaped defects", target: "0", status: "Active" },
    ],
    constraints: [],
    operating_policy: {},
    brand_voice: "",
    company_name: "",
    updated_at: null,
    updated_by: null,
    ...overrides,
  };
}

function setup(goals: CompanyGoals) {
  mockUseQuery.mockReturnValue({
    data: goals,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  });
  mockUseMutation.mockReturnValue({
    mutate: mockMutate,
    isPending: false,
  });
}

describe("GoalsTab — ObjectivesEditor grid", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the objectives container as a 2-column grid that collapses to 1 column on mobile", () => {
    setup(buildGoals());

    render(<GoalsTab />);

    const objectiveOne = screen.getByText("Objective #1");
    // The grid wrapper is the parent of each objective card.
    const gridContainer = objectiveOne.closest("div.rounded-lg")
      ?.parentElement as HTMLElement;

    expect(gridContainer).toHaveClass("grid");
    expect(gridContainer).toHaveClass("grid-cols-1");
    expect(gridContainer).toHaveClass("md:grid-cols-2");
  });

  it("keeps add/remove/edit objective behavior working", () => {
    setup(buildGoals());

    render(<GoalsTab />);

    // Two seeded objectives render as separate cards.
    expect(screen.getByText("Objective #1")).toBeInTheDocument();
    expect(screen.getByText("Objective #2")).toBeInTheDocument();

    // Edit a field on the first objective (both rows share the "metric" label,
    // so grab the first one — it belongs to Objective #1's input group).
    const metricInput = screen.getAllByLabelText(
      "metric",
    )[0] as HTMLInputElement;
    fireEvent.change(metricInput, { target: { value: "Updated metric" } });
    expect(metricInput.value).toBe("Updated metric");

    // Add a new objective row.
    fireEvent.click(screen.getByText("+ Add objective"));
    expect(screen.getByText("Objective #3")).toBeInTheDocument();

    // Remove the second objective row; remaining rows renumber to #1/#2.
    const removeButtons = screen.getAllByText("Remove");
    fireEvent.click(removeButtons[1]);
    expect(screen.getAllByText(/^Objective #/)).toHaveLength(2);
    expect(screen.queryByText("Objective #3")).not.toBeInTheDocument();
  });
});
