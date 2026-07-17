import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { SessionTrendChart } from "../session-trend-chart";
import { WorkSessionStatus } from "@/types";
import type { WorkSessionSummary } from "@/types";

function buildSession(overrides: Partial<WorkSessionSummary> = {}): WorkSessionSummary {
  return {
    id: "session-1",
    task_id: "11111111-1111-1111-1111-111111111111",
    branch_name: "feature/backend/ABC12345",
    status: WorkSessionStatus.ACTIVE,
    started_at: new Date().toISOString(),
    has_pr: false,
    ...overrides,
  };
}

describe("SessionTrendChart", () => {
  it("renders the card title", () => {
    render(<SessionTrendChart sessions={[buildSession()]} isLoading={false} />);
    expect(screen.getByText("Active Session Starts")).toBeInTheDocument();
  });

  it("shows an empty state when there are no sessions", () => {
    render(<SessionTrendChart sessions={[]} isLoading={false} />);
    expect(screen.getByText("No active sessions")).toBeInTheDocument();
  });

  it("shows an empty state when sessions is undefined", () => {
    render(<SessionTrendChart sessions={undefined} isLoading={false} />);
    expect(screen.getByText("No active sessions")).toBeInTheDocument();
  });

  it("does not show the empty state while loading", () => {
    render(<SessionTrendChart sessions={[]} isLoading />);
    expect(screen.queryByText("No active sessions")).not.toBeInTheDocument();
  });
});
