import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { SpawnWasteReport } from "@/types";

const { useZeroProgressSpawnWaste } = vi.hoisted(() => ({
  useZeroProgressSpawnWaste: vi.fn(),
}));

vi.mock("@/hooks/use-observability", () => ({
  useZeroProgressSpawnWaste,
}));

import { ZeroProgressSpawnWasteCard } from "../zero-progress-spawn-waste-card";

function buildReport(
  overrides: Partial<SpawnWasteReport> = {},
): SpawnWasteReport {
  return {
    total_sessions: 42,
    zero_progress_sessions: 7,
    zero_progress_cost_usd: 12.5,
    total_cost_usd: 125,
    zero_progress_cost_share: 0.1,
    by_agent: [
      {
        agent_slug: "be-dev-1",
        sessions: 20,
        zero_progress_sessions: 4,
        zero_progress_cost_usd: 8,
        rate: 0.2,
      },
      {
        agent_slug: "fe-dev-2",
        sessions: 22,
        zero_progress_sessions: 3,
        zero_progress_cost_usd: 4.5,
        rate: 0.13636363636363635,
      },
    ],
    by_team: [
      {
        team: "backend",
        sessions: 30,
        zero_progress_sessions: 5,
        zero_progress_cost_usd: 9,
        rate: 0.16666666666666666,
      },
      {
        team: "frontend",
        sessions: 12,
        zero_progress_sessions: 2,
        zero_progress_cost_usd: 3.5,
        rate: 0.16666666666666666,
      },
    ],
    by_task: [],
    ...overrides,
  };
}

describe("ZeroProgressSpawnWasteCard", () => {
  beforeEach(() => {
    useZeroProgressSpawnWaste.mockReset();
    useZeroProgressSpawnWaste.mockReturnValue({
      data: buildReport(),
      isLoading: false,
      isError: false,
    });
  });

  it("renders the distinct title and the distinguishing HelpTip copy", async () => {
    const user = userEvent.setup();
    render(<ZeroProgressSpawnWasteCard />);
    expect(
      screen.getByText("Zero-Progress Spawn Sessions (30d)"),
    ).toBeInTheDocument();
    // Radix Tooltip renders into a portal only when open.
    await user.hover(screen.getByText("Zero-Progress Spawn Sessions (30d)"));
    const tip = await screen.findByRole("tooltip");
    expect(tip).toHaveTextContent(/advanced nothing on their task/i);
    expect(tip).toHaveTextContent(
      /NOT the same as the Metrics page's 'Spawn Waste' card/i,
    );
    expect(tip).toHaveTextContent(/usage\/spawn-waste/i);
  });

  it("renders the headline numbers: total vs zero-progress sessions, cost, and cost share", () => {
    render(<ZeroProgressSpawnWasteCard />);
    expect(screen.getByText("10.0%")).toBeInTheDocument();
    expect(
      screen.getByText(
        (content) =>
          content.includes("7 of 42 sessions") &&
          content.includes("advanced nothing") &&
          content.includes("$12.50 of $125.00 spawn cost"),
      ),
    ).toBeInTheDocument();
  });

  it("renders by-agent rows", () => {
    render(<ZeroProgressSpawnWasteCard />);
    expect(screen.getByText("be-dev-1")).toBeInTheDocument();
    expect(screen.getByText("fe-dev-2")).toBeInTheDocument();
    const table = document.querySelector("table");
    expect(table).not.toBeNull();
    expect(table?.textContent).toContain("4");
    expect(table?.textContent).toContain("$8.00");
    expect(table?.textContent).toContain("20.0%");
  });

  it("renders by-team badges with sessions, cost, and rate", () => {
    const { container } = render(<ZeroProgressSpawnWasteCard />);
    const badges = Array.from(
      container.querySelectorAll("[data-testid='zero-progress-by-team'] > *"),
    );
    const badgeText = badges.map((b) => b.textContent?.replace(/\s+/g, " "));
    expect(badgeText.some((t) => t?.includes("Backend 5/30 · $9.00 · 16.7%"))).toBe(
      true,
    );
    expect(
      badgeText.some((t) => t?.includes("Frontend 2/12 · $3.50 · 16.7%")),
    ).toBe(true);
  });

  it("renders a skeleton while loading", () => {
    useZeroProgressSpawnWaste.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
    });
    const { container } = render(<ZeroProgressSpawnWasteCard />);
    expect(
      screen.getByText("Zero-Progress Spawn Sessions (30d)"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/advanced nothing/i),
    ).not.toBeInTheDocument();
    expect(container.querySelector(".animate-pulse")).not.toBeNull();
  });

  it("renders a muted empty state when there are no sessions", () => {
    useZeroProgressSpawnWaste.mockReturnValue({
      data: buildReport({
        total_sessions: 0,
        zero_progress_sessions: 0,
        zero_progress_cost_usd: 0,
        total_cost_usd: 0,
        zero_progress_cost_share: 0,
        by_agent: [],
        by_team: [],
      }),
      isLoading: false,
      isError: false,
    });
    render(<ZeroProgressSpawnWasteCard />);
    expect(
      screen.getByText(
        "No ended, task-scoped spawn sessions in this window yet.",
      ),
    ).toBeInTheDocument();
  });

  it("renders a one-line error message on failure", () => {
    useZeroProgressSpawnWaste.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
    });
    render(<ZeroProgressSpawnWasteCard />);
    expect(
      screen.getByText("Failed to load spawn-waste metrics."),
    ).toBeInTheDocument();
  });
});