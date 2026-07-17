import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { UsageTimeSeriesChart } from "../usage-time-series-chart";

describe("UsageTimeSeriesChart", () => {
  it("renders the card title", () => {
    render(<UsageTimeSeriesChart data={undefined} isLoading={false} />);
    expect(screen.getByText("Token Usage Over Time")).toBeInTheDocument();
  });

  it("shows an empty state when there is no data", () => {
    render(<UsageTimeSeriesChart data={[]} isLoading={false} />);
    expect(
      screen.getByText("No usage recorded in this window yet."),
    ).toBeInTheDocument();
  });

  it("does not show the empty state while loading", () => {
    render(<UsageTimeSeriesChart data={[]} isLoading />);
    expect(
      screen.queryByText("No usage recorded in this window yet."),
    ).not.toBeInTheDocument();
  });
});
