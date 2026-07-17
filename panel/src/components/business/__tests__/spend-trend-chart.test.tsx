import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { SpendTrendChart } from "../spend-trend-chart";
import type { UsageTimePoint } from "@/types";

function buildPoint(overrides: Partial<UsageTimePoint> = {}): UsageTimePoint {
  return {
    bucket: new Date().toISOString(),
    tokens_input: 1000,
    tokens_output: 500,
    total_tokens: 1500,
    cost_usd: 4.56,
    ...overrides,
  };
}

describe("SpendTrendChart", () => {
  it("renders the card title", () => {
    render(<SpendTrendChart data={[buildPoint()]} isLoading={false} />);
    expect(screen.getByText("Daily Spend (30d)")).toBeInTheDocument();
  });

  it("shows an empty state when there is no data", () => {
    render(<SpendTrendChart data={[]} isLoading={false} />);
    expect(screen.getByText("No spend data")).toBeInTheDocument();
  });

  it("shows an empty state when data is undefined", () => {
    render(<SpendTrendChart data={undefined} isLoading={false} />);
    expect(screen.getByText("No spend data")).toBeInTheDocument();
  });

  it("does not show the empty state while loading", () => {
    render(<SpendTrendChart data={[]} isLoading />);
    expect(screen.queryByText("No spend data")).not.toBeInTheDocument();
  });
});
