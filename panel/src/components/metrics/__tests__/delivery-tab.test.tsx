import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

const {
  mockCycleTime,
  mockBottlenecks,
  mockRework,
  mockProvenance,
  mockTeamScorecard,
} = vi.hoisted(() => ({
  mockCycleTime: vi.fn(),
  mockBottlenecks: vi.fn(),
  mockRework: vi.fn(),
  mockProvenance: vi.fn(),
  mockTeamScorecard: vi.fn(),
}));

vi.mock("@/hooks/use-observability", () => ({
  useCycleTime: mockCycleTime,
  useBottlenecks: mockBottlenecks,
  useRework: mockRework,
  useProvenance: mockProvenance,
  useTeamScorecard: mockTeamScorecard,
}));

import { DeliveryTabContent } from "../delivery-tab";

describe("DeliveryTabContent, Task Provenance card", () => {
  beforeEach(() => {
    mockCycleTime.mockReturnValue({ data: [], isLoading: false });
    mockBottlenecks.mockReturnValue({ data: undefined, isLoading: false });
    mockRework.mockReturnValue({
      data: {
        rate: 0,
        total_completed: 0,
        total_reworked: 0,
        by_team: [],
        by_agent: [],
        rework_cost_usd: 0,
      },
      isLoading: false,
    });
    mockTeamScorecard.mockReturnValue({ data: undefined, isLoading: false });
  });

  it("shows a loading skeleton, not the empty or error state, while fetching", () => {
    mockProvenance.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
    });
    render(<DeliveryTabContent />);
    expect(
      screen.queryByText(/no tasks created in this window/i),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(/failed to load provenance/i),
    ).not.toBeInTheDocument();
  });

  it("surfaces a load error instead of an endless skeleton", () => {
    mockProvenance.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
    });
    render(<DeliveryTabContent />);
    expect(
      screen.getByText(/failed to load provenance metrics/i),
    ).toBeInTheDocument();
  });

  it("shows a distinct empty state for a zero-task window", () => {
    mockProvenance.mockReturnValue({
      data: { total: 0, human_authored: 0, agent_authored: 0, human_rate: 0 },
      isLoading: false,
      isError: false,
    });
    render(<DeliveryTabContent />);
    expect(
      screen.getByText(/no tasks created in this window/i),
    ).toBeInTheDocument();
  });

  it("renders the human/agent split with both the percentage and absolute counts", () => {
    mockProvenance.mockReturnValue({
      data: {
        total: 238,
        human_authored: 19,
        agent_authored: 219,
        human_rate: 19 / 238,
      },
      isLoading: false,
      isError: false,
    });
    render(<DeliveryTabContent />);
    expect(screen.getByText("8.0%")).toBeInTheDocument();
    expect(screen.getByText(/19\/238 tasks/)).toBeInTheDocument();
    expect(screen.getByText("Human 19")).toBeInTheDocument();
    expect(screen.getByText("Agent 219")).toBeInTheDocument();
  });
});
