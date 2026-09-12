import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

const { mockPortfolio } = vi.hoisted(() => ({ mockPortfolio: vi.fn() }));

vi.mock("@/hooks/use-portfolio", () => ({
  usePortfolio: mockPortfolio,
}));

// Mock the panel's presented role so the real CeoGate can be exercised both as
// CEO and as an agent role.
const { mockRole } = vi.hoisted(() => ({ mockRole: vi.fn(() => "ceo") }));

vi.mock("../panel-role", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../panel-role")>();
  return { ...actual, currentPanelRole: mockRole };
});

import { PortfolioCards } from "../portfolio-cards";
import { CeoGate } from "../ceo-gate";

const TWO_PROJECTS = [
  {
    project_id: "11111111-1111-1111-1111-111111111111",
    project_slug: "roboco-api",
    project_name: "RoboCo API",
    active_task_count: 7,
    median_lead_time_hours: 18.2,
    rework_rate: 0.25,
    open_findings_count: 2,
    monthly_budget_burn_usd: 41.25,
  },
  {
    project_id: "22222222-1111-1111-1111-111111111111",
    project_slug: "discord-vexa-bridge",
    project_name: "Vexa Bridge",
    active_task_count: 3,
    median_lead_time_hours: null,
    rework_rate: 0,
    open_findings_count: 0,
    monthly_budget_burn_usd: 0,
  },
];

describe("PortfolioCards", () => {
  beforeEach(() => {
    mockRole.mockReturnValue("ceo");
    mockPortfolio.mockReturnValue({
      data: TWO_PROJECTS,
      isLoading: false,
      isError: false,
    });
  });

  it("renders one card per project with the five metrics", () => {
    render(<PortfolioCards />);
    expect(screen.getByText("RoboCo API")).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getByText("18.2h")).toBeInTheDocument();
    expect(screen.getByText("25%")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("$41.25")).toBeInTheDocument();
  });

  it("renders a null median lead time as an em dash", () => {
    render(<PortfolioCards />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("links each card to the project's task list by project id", () => {
    // The tasks page's project filter matches task.project_id, so the link
    // must carry the id — a slug yields an empty list on landing (the
    // drill-down regression this pins).
    render(<PortfolioCards />);
    expect(screen.getByRole("link", { name: /RoboCo API/ })).toHaveAttribute(
      "href",
      "/tasks?project=11111111-1111-1111-1111-111111111111",
    );
    expect(screen.getByRole("link", { name: /Vexa Bridge/ })).toHaveAttribute(
      "href",
      "/tasks?project=22222222-1111-1111-1111-111111111111",
    );
  });

  it("renders cards in the endpoint's most-active-first order without re-sorting", () => {
    render(<PortfolioCards />);
    const first = screen.getByText("RoboCo API");
    const second = screen.getByText("Vexa Bridge");
    // "RoboCo API" appears BEFORE "Vexa Bridge" in the DOM, exactly as the
    // endpoint delivered them.
    expect(
      first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("shows a skeleton matching the final card layout while loading", () => {
    mockPortfolio.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
    });
    const { container } = render(<PortfolioCards />);
    // 3 placeholder cards x (1 title + 5 metric rows) skeletons.
    expect(container.querySelectorAll('[data-slot="skeleton"]').length).toBe(
      18,
    );
  });

  it("shows an empty state when the fleet is empty", () => {
    mockPortfolio.mockReturnValue({
      data: [],
      isLoading: false,
      isError: false,
    });
    const { container } = render(<PortfolioCards />);
    expect(
      screen.getByText(/no projects in the portfolio/i),
    ).toBeInTheDocument();
    expect(container.querySelectorAll('[data-slot="skeleton"]').length).toBe(0);
  });

  it("shows an error message instead of an endless skeleton", () => {
    mockPortfolio.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
    });
    const { container } = render(<PortfolioCards />);
    expect(
      screen.getByText(/failed to load portfolio metrics/i),
    ).toBeInTheDocument();
    expect(container.querySelectorAll('[data-slot="skeleton"]').length).toBe(0);
  });
});

describe("PortfolioCards CEO gating (as wired inside CeoGate)", () => {
  beforeEach(() => {
    mockPortfolio.mockClear();
    mockPortfolio.mockReturnValue({
      data: TWO_PROJECTS,
      isLoading: false,
      isError: false,
    });
  });

  it("renders the section for the CEO", () => {
    render(
      <CeoGate>
        <PortfolioCards />
      </CeoGate>,
    );
    expect(screen.getByText("RoboCo API")).toBeInTheDocument();
  });

  it("renders nothing for an agent role — the section never mounts", () => {
    mockRole.mockReturnValue("developer");
    const { container } = render(
      <CeoGate>
        <PortfolioCards />
      </CeoGate>,
    );
    expect(container).toBeEmptyDOMElement();
    // The gated component never mounts, so the CEO-only endpoint is never
    // fetched from an agent-role session.
    expect(mockPortfolio).not.toHaveBeenCalled();
  });
});
