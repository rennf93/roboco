import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { CostTrendChart } from "../cost-trend-chart";
import type { UsageTimePoint } from "@/types";

function buildPoint(overrides: Partial<UsageTimePoint> = {}): UsageTimePoint {
  return {
    bucket: new Date().toISOString(),
    tokens_input: 1000,
    tokens_output: 500,
    total_tokens: 1500,
    cost_usd: 1.23,
    ...overrides,
  };
}

describe("CostTrendChart", () => {
  it("renders the card title", () => {
    render(<CostTrendChart data={[buildPoint()]} isLoading={false} />);
    expect(screen.getByText("Spend Trend (7d)")).toBeInTheDocument();
  });

  it("shows an empty state when there is no data", () => {
    render(<CostTrendChart data={[]} isLoading={false} />);
    expect(screen.getByText("No usage data")).toBeInTheDocument();
  });

  it("shows an empty state when data is undefined", () => {
    render(<CostTrendChart data={undefined} isLoading={false} />);
    expect(screen.getByText("No usage data")).toBeInTheDocument();
  });

  it("does not show the empty state while loading", () => {
    render(<CostTrendChart data={[]} isLoading />);
    expect(screen.queryByText("No usage data")).not.toBeInTheDocument();
  });
});
